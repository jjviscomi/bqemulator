"""Catalog auto-sync for SQL-DDL-created tables and views.

The SQL translation pipeline runs ``CREATE TABLE [OR REPLACE] foo
[AS SELECT ...]`` (or the ``VIEW`` analogue) through DuckDB but does
not — until this module — update the catalog cache. The conformance
corpus's setup statements rely on these DDL forms to seed test data;
downstream queries — especially versioning DDL (``CREATE SNAPSHOT
TABLE``, ``CREATE TABLE … CLONE``, ``CREATE MATERIALIZED VIEW``) and
the row-access rewriter (which needs ``table_type='VIEW'`` +
``view_query`` to apply caller-bound policies *through* the view body)
— expect to find the source via
:meth:`CatalogRepository.get_table`.

Call :func:`sync_created_table` after a successful ``CREATE TABLE``
execution; it introspects the freshly-created DuckDB table, builds a
minimal :class:`TableMeta`, and upserts it into the catalog. The
detection is conservative: only the plain ``CREATE [OR REPLACE] TABLE
name [(col …)|AS SELECT …]`` shape registers — VIEW, MATERIALIZED
VIEW, CLONE, and SNAPSHOT forms route elsewhere.

Call :func:`sync_created_view` for ``CREATE [OR REPLACE] VIEW`` —
this stores the view body verbatim under ``view_query`` so the
row-access rewriter's ``_expand_view`` branch can recurse and apply
caller-bound policies on the base table the view reads. Materialized
views are handled by the versioning DDL manager and are not synced
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa
import sqlglot
from sqlglot import exp

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import (
    DatasetMeta,
    FieldMode,
    TableFieldSchema,
    TableMeta,
    TableSchema,
    TimePartitioning,
)
from bqemulator.storage.sql_identifiers import quoted_table_ref

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext


# Part counts for ``project.dataset.table`` references.
_PARTS_FULLY_QUALIFIED = 3
_PARTS_DATASET_QUALIFIED = 2


def sync_created_table(bq_sql: str, project_id: str, ctx: AppContext) -> None:
    """Register or refresh a ``CREATE [OR REPLACE] TABLE`` output in the catalog.

    No-op if ``bq_sql`` is not a plain CREATE TABLE form (VIEW, MV,
    CLONE, SNAPSHOT, or unparseable SQL all return early). Idempotent
    under ``CREATE OR REPLACE`` — an existing catalog row is updated
    in place with the fresh schema and row count.
    """
    target = _detect_plain_create_table(bq_sql)
    if target is None:
        return
    p_id, d_id, t_id = _split_target(target, project_id)
    if not d_id or not t_id:
        return
    if ctx.catalog.get_dataset(p_id, d_id) is None:
        # The dataset was never registered via REST — register it now so
        # a table created purely via SQL DDL (e.g. ``CREATE SCHEMA ds``
        # then ``CREATE TABLE ds.t AS …``) becomes catalog-visible,
        # matching real BigQuery where both surface via
        # INFORMATION_SCHEMA and the REST API. Without this, the table
        # lives in DuckDB but has no ``TableMeta``, so catalog consumers
        # (INFORMATION_SCHEMA, ``tables.list``, row-access-policy target
        # validation) can't see it.
        _ensure_dataset(p_id, d_id, ctx)
    schema = _introspect_schema(p_id, d_id, t_id, ctx)
    num_rows = _introspect_num_rows(p_id, d_id, t_id, ctx)
    extras = _extract_ddl_metadata(bq_sql)
    now = ctx.clock.now()
    meta = TableMeta(
        project_id=p_id,
        dataset_id=d_id,
        table_id=t_id,
        table_type="TABLE",
        schema=schema,
        description=extras.description,
        time_partitioning=extras.time_partitioning,
        creation_time=now,
        last_modified_time=now,
        num_rows=num_rows,
        num_bytes=0,
        etag=generate_etag(p_id, d_id, t_id, "TABLE", str(now)),
    )
    if ctx.catalog.get_table(p_id, d_id, t_id) is not None:
        ctx.catalog.update_table(meta)
    else:
        ctx.catalog.create_table(meta)


def sync_created_view(bq_sql: str, project_id: str, ctx: AppContext) -> None:
    """Register or refresh a ``CREATE [OR REPLACE] VIEW`` output in the catalog.

    No-op if ``bq_sql`` is not a plain CREATE VIEW form (TABLE,
    MATERIALIZED VIEW, or unparseable SQL all return early). Idempotent
    under ``CREATE OR REPLACE`` — an existing catalog row is updated
    in place with the fresh schema and view body.

    The catalog entry's ``view_query`` field carries the view's
    SELECT body verbatim in BigQuery dialect. The row-access rewriter
    re-parses this body when it walks a view reference, so the body
    must round-trip cleanly through SQLGlot — we re-serialise it via
    ``body.sql(dialect='bigquery')`` here to canonicalise quoting.
    """
    target, body_node = _detect_plain_create_view(bq_sql)
    if target is None or body_node is None:
        return
    p_id, d_id, t_id = _split_target(target, project_id)
    if not d_id or not t_id:
        return
    if ctx.catalog.get_dataset(p_id, d_id) is None:
        # Register the dataset so a view created purely via SQL DDL is
        # catalog-visible (see ``sync_created_table`` for rationale).
        _ensure_dataset(p_id, d_id, ctx)
    schema = _introspect_schema(p_id, d_id, t_id, ctx)
    # Re-serialise the body in BigQuery dialect so downstream re-parsing
    # (by the row-access rewriter's ``_expand_view``) sees canonical
    # syntax without surface-level surprises.
    body_sql = body_node.sql(dialect="bigquery")
    now = ctx.clock.now()
    meta = TableMeta(
        project_id=p_id,
        dataset_id=d_id,
        table_id=t_id,
        table_type="VIEW",
        schema=schema,
        view_query=body_sql,
        creation_time=now,
        last_modified_time=now,
        # Views carry no physical rows; report zero so downstream
        # consumers don't accidentally try to count rows via
        # ``SELECT COUNT(*) FROM view`` (which can be expensive).
        num_rows=0,
        num_bytes=0,
        etag=generate_etag(p_id, d_id, t_id, "VIEW", str(now)),
    )
    if ctx.catalog.get_table(p_id, d_id, t_id) is not None:
        ctx.catalog.update_table(meta)
    else:
        ctx.catalog.create_table(meta)


def sync_created_schema(bq_sql: str, project_id: str, ctx: AppContext) -> None:
    """Register a catalog dataset for a SQL ``CREATE SCHEMA`` statement.

    No-op if ``bq_sql`` is not a ``CREATE SCHEMA`` form (or is
    unparseable) and idempotent — an existing dataset is left
    untouched. BigQuery's ``CREATE SCHEMA`` (a.k.a. ``CREATE DATASET``)
    creates a dataset that is fully visible via INFORMATION_SCHEMA and
    the REST API; registering it here keeps SQL-created datasets in
    parity with REST-created ones (``datasets.insert``). Call after a
    successful ``CREATE SCHEMA`` execution.
    """
    detected = _detect_create_schema(bq_sql, project_id)
    if detected is None:
        return
    p_id, d_id = detected
    if not d_id:
        return
    _ensure_dataset(p_id, d_id, ctx)


def _unwrap_table_target(target: exp.Expression | None) -> exp.Table | None:
    """Unwrap an ``exp.Schema`` shell and return the inner :class:`exp.Table`, or ``None``."""
    if isinstance(target, exp.Schema):
        target = target.this
    return target if isinstance(target, exp.Table) else None


def _split_table_ref_parts(target: exp.Table) -> list[str]:
    """Return the non-empty ``(catalog, db, name)`` parts.

    A whole-backticked dotted name (`` `proj.ds` ``) lands as a single
    component and is split on the dot so the caller sees the same
    parts shape as the per-component-backticked form.
    """
    parts = [p for p in (target.catalog, target.db, target.name) if p]
    # Whole-backticked ``\`proj.ds\``` lands as one dotted name.
    if len(parts) == 1 and "." in parts[0]:
        return parts[0].split(".")
    return parts


def _detect_create_schema(bq_sql: str, default_project: str) -> tuple[str, str] | None:
    """Return ``(project_id, dataset_id)`` for a ``CREATE SCHEMA`` statement.

    Returns ``None`` for any non-``CREATE SCHEMA`` form or unparseable
    SQL. Handles bare (``ds``), project-qualified (``proj.ds``),
    per-component-backticked (`` `proj`.`ds` ``), and whole-backticked
    (`` `proj.ds` ``) shapes.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — not CREATE SCHEMA / unparseable
        return None
    if not isinstance(tree, exp.Create) or (tree.kind or "").upper() != "SCHEMA":
        return None
    target = _unwrap_table_target(tree.this)
    if target is None:
        return None
    parts = _split_table_ref_parts(target)
    if len(parts) == _PARTS_DATASET_QUALIFIED:
        return parts[0], parts[1]
    if len(parts) == 1:
        return default_project, parts[0]
    return None


def _ensure_dataset(project_id: str, dataset_id: str, ctx: AppContext) -> None:
    """Register a minimal catalog dataset if one doesn't already exist.

    Idempotent. Mirrors the default-valued :class:`DatasetMeta` that
    ``datasets.insert`` builds for a bare ``{datasetReference: …}`` body
    (US location, no labels). Used by the SQL-DDL sync helpers so a
    table, view, or schema created via SQL is catalog-visible even when
    the dataset was never registered via REST.
    """
    if ctx.catalog.get_dataset(project_id, dataset_id) is not None:
        return
    now = ctx.clock.now()
    ctx.catalog.create_dataset(
        DatasetMeta(
            project_id=project_id,
            dataset_id=dataset_id,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project_id, dataset_id, str(now)),
        ),
    )


def _is_materialized_view(tree: exp.Create) -> bool:
    """Return ``True`` when the parsed ``CREATE`` tree carries a ``MATERIALIZED`` property."""
    properties = tree.args.get("properties")
    if not isinstance(properties, exp.Properties):
        return False
    return any(isinstance(p, exp.MaterializedProperty) for p in properties.expressions)


def _detect_plain_create_view(
    bq_sql: str,
) -> tuple[exp.Table | None, exp.Expression | None]:
    """Return ``(target_table, body_expression)`` for a plain ``CREATE VIEW``.

    Returns ``(None, None)`` for TABLE, MATERIALIZED VIEW, or
    unparseable SQL. The body expression is the SELECT (or set-op)
    that follows ``AS`` and is re-serialised verbatim into the
    ``view_query`` field of the synthesised :class:`TableMeta`.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(tree, exp.Create):
        return None, None
    if (tree.kind or "").upper() != "VIEW":
        return None, None
    # MATERIALIZED VIEW → route via the MV manager instead of syncing here.
    if _is_materialized_view(tree):
        return None, None
    target = _unwrap_table_target(tree.this)
    if target is None:
        return None, None
    body = tree.expression
    if body is None:
        return None, None
    return target, body


def _detect_plain_create_table(bq_sql: str) -> exp.Table | None:
    """Return the target :class:`exp.Table` for a plain ``CREATE TABLE``.

    Returns ``None`` for VIEW, MATERIALIZED VIEW, CLONE, SNAPSHOT, or
    unparseable SQL.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(tree, exp.Create):
        return None
    if (tree.kind or "").upper() != "TABLE":
        return None
    if tree.args.get("clone") is not None:
        # ``CREATE TABLE x CLONE y`` — versioning DDL routes this.
        return None
    target = tree.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
        return None
    return target


def _split_target(table: exp.Table, default_project: str) -> tuple[str, str, str]:
    """Split a SQLGlot :class:`Table` into (project, dataset, table) ids."""
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    if len(parts) == _PARTS_FULLY_QUALIFIED:
        return parts[0], parts[1], parts[2]
    if len(parts) == _PARTS_DATASET_QUALIFIED:
        return default_project, parts[0], parts[1]
    return default_project, "", parts[0] if parts else ""


def _introspect_schema(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: AppContext,
) -> TableSchema:
    """Build a :class:`TableSchema` from the freshly-created DuckDB table."""
    from bqemulator.storage.arrow_bridge import (
        introspect_arrow_schema,
    )

    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    schema = introspect_arrow_schema(ctx.engine, target_ref)
    # DuckDB's Arrow exporter always sets ``nullable=True`` regardless
    # of the SQL NOT NULL constraint, so we cross-reference
    # ``PRAGMA table_info`` (which preserves the constraint in its
    # ``notnull`` column) to recover the REQUIRED / NULLABLE mode.
    # Without this, ``INFORMATION_SCHEMA.COLUMNS`` returns
    # ``is_nullable='YES'`` for every column. Pinned by
    # ``information_schema/is_columns_basic``.
    notnull_by_col = _column_notnull_map(target_ref, ctx)
    fields = tuple(
        _arrow_field_to_table_field(
            schema.field(i),
            mode_override=("REQUIRED" if notnull_by_col.get(schema.field(i).name) else "NULLABLE"),
        )
        for i in range(len(schema))
    )
    return TableSchema(fields=fields)


def _arrow_field_to_table_field(  # type: ignore[no-any-unimported]
    arrow_field: pa.Field,
    mode_override: FieldMode | None = None,
) -> TableFieldSchema:
    """Recursively render an Arrow field into a BigQuery ``TableFieldSchema``.

    Three shapes:

    * **List / large-list** → BigQuery REPEATED mode; the inner Arrow
      type drives the resulting ``type``. Nested structs inside a list
      recurse through this same helper.
    * **Struct** → BigQuery ``RECORD`` type with the nested
      ``TableFieldSchema`` tuple recursively populated.
    * **Scalar** → BigQuery scalar type via
      :func:`arrow_type_to_bq_type_name`.

    The ``mode_override`` argument carries the catalog's NULL/NOT NULL
    flag from ``PRAGMA table_info`` for top-level columns; nested
    fields inside a STRUCT/REPEATED column always default to NULLABLE
    because BigQuery doesn't surface a per-nested-field NOT NULL flag
    in ``INFORMATION_SCHEMA.COLUMNS``.

    Pinned by ``information_schema/is_columns_with_struct_field``.
    """
    from bqemulator.storage.arrow_bridge import arrow_type_to_bq_type_name

    arrow_type = arrow_field.type
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        element_field = arrow_type.value_field
        inner = _arrow_field_to_table_field(element_field)
        return TableFieldSchema(
            name=arrow_field.name,
            type=inner.type,
            mode="REPEATED",
            fields=inner.fields,
        )
    if pa.types.is_struct(arrow_type):
        nested = tuple(
            _arrow_field_to_table_field(arrow_type.field(i)) for i in range(arrow_type.num_fields)
        )
        return TableFieldSchema(
            name=arrow_field.name,
            type="RECORD",
            mode=mode_override or "NULLABLE",
            fields=nested,
        )
    return TableFieldSchema(
        name=arrow_field.name,
        type=arrow_type_to_bq_type_name(arrow_type),
        mode=mode_override or "NULLABLE",
    )


def _column_notnull_map(target_ref: str, ctx: AppContext) -> dict[str, bool]:
    """Return ``{column_name: notnull}`` from DuckDB's ``PRAGMA table_info``.

    DuckDB's ``PRAGMA table_info`` rows are
    ``(cid, name, type, notnull, dflt_value, pk)``. We project columns 1
    (name) and 3 (notnull) — both stable across DuckDB versions.
    """
    rows = ctx.engine.execute(f"PRAGMA table_info({target_ref})").fetchall()
    return {row[1]: bool(row[3]) for row in rows}


def _introspect_num_rows(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: AppContext,
) -> int:
    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    row = ctx.engine.execute(f"SELECT COUNT(*) FROM {target_ref}").fetchone()
    return int(row[0]) if row else 0


@dataclass(frozen=True)
class _DdlExtras:
    """Optional ``TableMeta`` fields extracted from a CREATE TABLE DDL.

    ``description`` and ``time_partitioning`` are populated from the
    SQLGlot ``Create`` AST when the DDL carries the corresponding
    BigQuery clauses (``OPTIONS(description=…)``,
    ``PARTITION BY <col>``, ``OPTIONS(require_partition_filter=TRUE)``).
    Missing clauses leave the fields ``None`` (the catalog default).
    """

    description: str | None = None
    time_partitioning: TimePartitioning | None = None


def _extract_ddl_metadata(bq_sql: str) -> _DdlExtras:
    """Pull description + partitioning out of a CREATE TABLE statement.

    Closes three INFORMATION_SCHEMA conformance gaps in one pass:
    ``is_columns_partitioning_column`` (needs ``time_partitioning.field``),
    ``is_table_options_basic`` / ``is_table_options_description`` /
    ``is_table_options_partition_filter`` (need ``description`` +
    ``time_partitioning.require_partition_filter``).
    """
    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return _DdlExtras()
    if not isinstance(parsed, exp.Create):
        return _DdlExtras()
    properties = parsed.args.get("properties")
    if properties is None:
        return _DdlExtras()

    partition_field = _ddl_partition_field(properties)
    options = _ddl_table_options(properties)
    return _DdlExtras(
        description=options.description,
        time_partitioning=_build_time_partitioning(partition_field, options),
    )


@dataclass(frozen=True)
class _DdlOptions:
    """Subset of ``OPTIONS(...)`` clauses the catalog tracks today."""

    description: str | None = None
    require_partition_filter: bool = False
    expiration_ms: int | None = None


def _ddl_partition_field(properties: exp.Properties) -> str | None:
    """Return the ``PARTITION BY <col>`` field name, if any.

    ``_PARTITIONDATE`` / ``_PARTITIONTIME`` are ingestion-time
    pseudo-columns; BigQuery's contract is
    ``time_partitioning.field=None`` for them. Function-form
    (``PARTITION BY DATE(ts)``) and range-form land in
    ``range_partitioning`` and are out of scope here.
    """
    for prop in properties.expressions:
        if not isinstance(prop, exp.PartitionedByProperty):
            continue
        inner = prop.this
        if not isinstance(inner, exp.Identifier):
            continue
        name = inner.name
        if name.upper() in {"_PARTITIONDATE", "_PARTITIONTIME"}:
            continue
        return name
    return None


def _ddl_table_options(properties: exp.Properties) -> _DdlOptions:
    """Return the subset of ``OPTIONS(...)`` clauses the catalog tracks."""
    description: str | None = None
    require_partition_filter = False
    expiration_ms: int | None = None
    for prop in properties.expressions:
        if not isinstance(prop, exp.Property):
            continue
        key = prop.this.name.lower() if prop.this else ""
        value = prop.args.get("value")
        if key == "description" and isinstance(value, exp.Literal):
            description = value.this
        elif key == "require_partition_filter" and isinstance(value, exp.Boolean):
            require_partition_filter = bool(value.this)
        elif key == "partition_expiration_days" and isinstance(value, exp.Literal):
            expiration_ms = _days_literal_to_ms(value.this)
    return _DdlOptions(
        description=description,
        require_partition_filter=require_partition_filter,
        expiration_ms=expiration_ms,
    )


def _days_literal_to_ms(raw: object) -> int | None:
    """Convert an ``OPTIONS(partition_expiration_days=N)`` literal to ms."""
    if not isinstance(raw, (str, int, float)):
        return None
    try:
        return int(float(raw) * 24 * 60 * 60 * 1000)
    except (TypeError, ValueError):
        return None


def _build_time_partitioning(
    partition_field: str | None,
    options: _DdlOptions,
) -> TimePartitioning | None:
    if (
        partition_field is None
        and not options.require_partition_filter
        and options.expiration_ms is None
    ):
        return None
    return TimePartitioning(
        type="DAY",
        field=partition_field,
        expiration_ms=options.expiration_ms,
        require_partition_filter=options.require_partition_filter,
    )


__all__ = ["sync_created_table", "sync_created_view"]
