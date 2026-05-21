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

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import (
    TableFieldSchema,
    TableMeta,
    TableSchema,
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
        # No matching dataset — the DDL ran in DuckDB but we have
        # nowhere to register the metadata. Synthetic unit-test paths
        # exercise this; conformance setup always creates the dataset
        # via REST before issuing SQL DDL.
        return
    schema = _introspect_schema(p_id, d_id, t_id, ctx)
    num_rows = _introspect_num_rows(p_id, d_id, t_id, ctx)
    now = ctx.clock.now()
    meta = TableMeta(
        project_id=p_id,
        dataset_id=d_id,
        table_id=t_id,
        table_type="TABLE",
        schema=schema,
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
        return
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
    # ``materialized`` property → MATERIALIZED VIEW; route via the MV
    # manager instead of syncing here.
    properties = tree.args.get("properties")
    if isinstance(properties, exp.Properties):
        for prop in properties.expressions:
            if isinstance(prop, exp.MaterializedProperty):
                return None, None
    target = tree.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
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
        arrow_type_to_bq_type_name,
        introspect_arrow_schema,
    )

    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    schema = introspect_arrow_schema(ctx.engine, target_ref)
    fields = tuple(
        TableFieldSchema(
            name=schema.field(i).name,
            type=arrow_type_to_bq_type_name(schema.field(i).type),
            mode="NULLABLE",
        )
        for i in range(len(schema))
    )
    return TableSchema(fields=fields)


def _introspect_num_rows(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: AppContext,
) -> int:
    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    row = ctx.engine.execute(f"SELECT COUNT(*) FROM {target_ref}").fetchone()
    return int(row[0]) if row else 0


__all__ = ["sync_created_table", "sync_created_view"]
