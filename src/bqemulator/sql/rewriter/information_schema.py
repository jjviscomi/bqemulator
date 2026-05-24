"""``INFORMATION_SCHEMA`` rewriter.

BigQuery exposes a family of virtual tables under
``{project}.{dataset}.INFORMATION_SCHEMA.*`` that describe the catalog.

Supported views: ``ROUTINES``, ``MATERIALIZED_VIEWS``,
``ROW_ACCESS_POLICIES``, ``SCHEMATA``, ``TABLES``, ``COLUMNS``,
``TABLE_OPTIONS``, ``VIEWS``, ``PARTITIONS``.

This rewriter runs as a PRE-translation pass on the original BigQuery
SQL (same stage as the wildcard expander). When it detects a reference
to ``INFORMATION_SCHEMA.<view>``, it replaces the reference with an
inline ``VALUES`` subquery materialised from the current catalog state.
Scope-qualified forms are supported:

- ``{project}.{dataset}.INFORMATION_SCHEMA.<view>``
- ``{dataset}.INFORMATION_SCHEMA.<view>``
- ``INFORMATION_SCHEMA.<view>``

Materialising inline via VALUES keeps the pipeline simple and avoids
dragging DuckDB catalog tables into a user-facing view. The columns
match BigQuery's published INFORMATION_SCHEMA schemas, with
unsupported columns (SQL security, owner, etc.) returned as NULL.
``INFORMATION_SCHEMA.JOBS`` and the ``JOBS_BY_*`` family are
deliberately out of scope — see
``docs/reference/out-of-scope.md#information_schemajobs-family``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from bqemulator.catalog.models import (
        DatasetMeta,
        MaterializedViewMeta,
        PartitionMeta,
        RoutineMeta,
        RowAccessPolicyMeta,
        TableFieldSchema,
        TableMeta,
    )
    from bqemulator.catalog.repository import CatalogRepository


def _build_patterns(view_name: str) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """Compile the three matching patterns for one INFORMATION_SCHEMA view.

    Each pattern accepts a single trailing ``` ` ``` after the view name
    so a fully-quoted reference like
    ``` `dataset.INFORMATION_SCHEMA.TABLES` ``` is consumed cleanly
    (without the trailing backtick, the substitution leaves a stray
    closing backtick that breaks the downstream SQLGlot tokeniser).
    """
    escaped = re.escape(view_name.upper())
    project_ds = re.compile(
        r"`?(?P<project>[A-Za-z0-9_\-]+)`?\s*\.\s*"
        r"`?(?P<dataset>[A-Za-z0-9_\-]+)`?\s*\.\s*"
        rf"INFORMATION_SCHEMA\s*\.\s*{escaped}`?",
        flags=re.IGNORECASE,
    )
    ds = re.compile(
        r"`?(?P<dataset>[A-Za-z0-9_\-]+)`?\s*\.\s*"
        rf"INFORMATION_SCHEMA\s*\.\s*{escaped}`?",
        flags=re.IGNORECASE,
    )
    bare = re.compile(
        rf"INFORMATION_SCHEMA\s*\.\s*{escaped}`?",
        flags=re.IGNORECASE,
    )
    return project_ds, ds, bare


_ROUTINES_PROJECT_DS, _ROUTINES_DS, _ROUTINES_BARE = _build_patterns("ROUTINES")
_MV_PROJECT_DS, _MV_DS, _MV_BARE = _build_patterns("MATERIALIZED_VIEWS")
_RAP_PROJECT_DS, _RAP_DS, _RAP_BARE = _build_patterns("ROW_ACCESS_POLICIES")
_SCHEMATA_PROJECT_DS, _SCHEMATA_DS, _SCHEMATA_BARE = _build_patterns("SCHEMATA")
_TABLES_PROJECT_DS, _TABLES_DS, _TABLES_BARE = _build_patterns("TABLES")
_COLUMNS_PROJECT_DS, _COLUMNS_DS, _COLUMNS_BARE = _build_patterns("COLUMNS")
_TABLE_OPTIONS_PROJECT_DS, _TABLE_OPTIONS_DS, _TABLE_OPTIONS_BARE = _build_patterns(
    "TABLE_OPTIONS",
)
_VIEWS_PROJECT_DS, _VIEWS_DS, _VIEWS_BARE = _build_patterns("VIEWS")
_PARTITIONS_PROJECT_DS, _PARTITIONS_DS, _PARTITIONS_BARE = _build_patterns("PARTITIONS")


def expand_information_schema(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace every supported ``INFORMATION_SCHEMA.*`` virtual table inline.

    Chains the nine view expanders in a fixed order (``ROUTINES`` →
    ``MATERIALIZED_VIEWS`` → ``ROW_ACCESS_POLICIES`` → ``SCHEMATA`` →
    ``TABLES`` → ``COLUMNS`` → ``TABLE_OPTIONS`` → ``VIEWS`` →
    ``PARTITIONS``) so each pass operates on the output of the
    previous one.
    """
    out = expand_information_schema_routines(bq_sql, project_id, catalog)
    out = expand_information_schema_materialized_views(out, project_id, catalog)
    out = expand_information_schema_row_access_policies(out, project_id, catalog)
    out = expand_information_schema_schemata(out, project_id, catalog)
    out = expand_information_schema_tables(out, project_id, catalog)
    out = expand_information_schema_columns(out, project_id, catalog)
    out = expand_information_schema_table_options(out, project_id, catalog)
    out = expand_information_schema_views(out, project_id, catalog)
    return expand_information_schema_partitions(out, project_id, catalog)


def expand_information_schema_routines(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.ROUTINES`` references with inline VALUES."""
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        routines = catalog.list_routines(proj, dataset)
        return _routines_as_values(routines, proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        routines = catalog.list_routines(project_id, dataset)
        return _routines_as_values(routines, project_id, dataset)

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_routines_values_subquery()

    out = _ROUTINES_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _ROUTINES_DS.sub(replace_ds, out)
    return _ROUTINES_BARE.sub(replace_bare, out)


def expand_information_schema_materialized_views(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.MATERIALIZED_VIEWS`` with inline VALUES."""
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        views = catalog.list_materialized_views(proj, dataset)
        return _mv_as_values(views, proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        views = catalog.list_materialized_views(project_id, dataset)
        return _mv_as_values(views, project_id, dataset)

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_mv_values_subquery()

    out = _MV_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _MV_DS.sub(replace_ds, out)
    return _MV_BARE.sub(replace_bare, out)


# ---------------------------------------------------------------------------
# ROUTINES
# ---------------------------------------------------------------------------


def _routines_as_values(
    routines: tuple[RoutineMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    """Build a VALUES subquery with the BigQuery INFORMATION_SCHEMA.ROUTINES schema."""
    columns = (
        "specific_catalog",
        "specific_schema",
        "specific_name",
        "routine_catalog",
        "routine_schema",
        "routine_name",
        "routine_type",
        "language",
        "routine_body",
        "data_type",
        "created",
        "last_altered",
        "ddl",
    )
    if not routines:
        col_list = ", ".join(columns)
        return (
            f"(SELECT * FROM (VALUES "
            f"(NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
            f") AS v({col_list}) WHERE FALSE)"
        )

    rows: list[str] = []
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    for r in routines:
        rid = _sql_literal(r.routine_id)
        return_type = _render_bq_type(r.return_type) if r.return_type else "NULL"
        created_ts = _ts_literal(int(r.creation_time.timestamp() * 1000))
        altered_ts = _ts_literal(int(r.last_modified_time.timestamp() * 1000))
        ddl = _sql_literal(_synth_ddl(r))
        routine_body = _sql_literal(
            "EXTERNAL" if r.language == "JAVASCRIPT" else "SQL",
        )
        rows.append(
            f"({pid}, {did}, {rid}, {pid}, {did}, {rid}, "
            f"{_sql_literal(r.routine_type)}, {_sql_literal(r.language)}, "
            f"{routine_body}, {return_type}, {created_ts}, {altered_ts}, {ddl})",
        )
    col_list = ", ".join(columns)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_routines_values_subquery() -> str:
    return (
        "(SELECT * FROM (VALUES (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
        "NULL, NULL, NULL, NULL, NULL)) AS v(specific_catalog, specific_schema, "
        "specific_name, routine_catalog, routine_schema, routine_name, routine_type, "
        "language, routine_body, data_type, created, last_altered, ddl) "
        "WHERE FALSE)"
    )


def _render_bq_type(bq_type: dict[str, object]) -> str:
    kind = str(bq_type.get("typeKind", ""))
    return _sql_literal(kind) if kind else "NULL"


def _synth_ddl(r: RoutineMeta) -> str:
    """Synthesise a CREATE-like DDL string for the INFORMATION_SCHEMA output."""
    args = ", ".join(a.name for a in r.arguments)
    kind = r.routine_type
    if kind == "TABLE_VALUED_FUNCTION":
        return f"CREATE TABLE FUNCTION {r.dataset_id}.{r.routine_id}({args}) AS (...)"
    if kind == "PROCEDURE":
        return f"CREATE PROCEDURE {r.dataset_id}.{r.routine_id}({args}) BEGIN ... END"
    return f"CREATE FUNCTION {r.dataset_id}.{r.routine_id}({args}) AS (...)"


# ---------------------------------------------------------------------------
# MATERIALIZED_VIEWS
# ---------------------------------------------------------------------------


_MV_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "last_refresh_time",
    "refresh_watermark",
    "enable_refresh",
    "refresh_interval_minutes",
    "last_modified_time",
    "is_stale",
)


def _mv_as_values(
    views: tuple[MaterializedViewMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    """Build a VALUES subquery with the INFORMATION_SCHEMA.MATERIALIZED_VIEWS shape."""
    if not views:
        return _empty_mv_values_subquery()
    rows: list[str] = []
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    for v in views:
        tid = _sql_literal(v.table_id)
        last_refresh = _ts_literal(int(v.last_refresh_time.timestamp() * 1000))
        is_stale = "TRUE" if v.is_stale else "FALSE"
        rows.append(
            f"({pid}, {did}, {tid}, {last_refresh}, {last_refresh}, "
            f"TRUE, 30, {last_refresh}, {is_stale})",
        )
    col_list = ", ".join(_MV_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_mv_values_subquery() -> str:
    col_list = ", ".join(_MV_COLUMNS)
    return (
        "(SELECT * FROM (VALUES ("
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL"
        f")) AS v({col_list}) WHERE FALSE)"
    )


# ---------------------------------------------------------------------------
# ROW_ACCESS_POLICIES
# ---------------------------------------------------------------------------


_RAP_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "policy_name",
    "grantees",
    "filter_predicate",
    "creation_time",
    "last_modified_time",
)


def expand_information_schema_row_access_policies(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`` references inline.

    The published BigQuery schema is project- or dataset-scoped; bare
    references (no qualifier) return an empty result set. The grantees
    column is materialised as a comma-separated string — that's how
    BigQuery surfaces it through SQL.
    """
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        rows = _policies_for_dataset(catalog, proj, dataset)
        return _rap_as_values(rows, proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        rows = _policies_for_dataset(catalog, project_id, dataset)
        return _rap_as_values(rows, project_id, dataset)

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_rap_values_subquery()

    out = _RAP_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _RAP_DS.sub(replace_ds, out)
    return _RAP_BARE.sub(replace_bare, out)


def _policies_for_dataset(
    catalog: CatalogRepository,
    project_id: str,
    dataset_id: str,
) -> tuple[RowAccessPolicyMeta, ...]:
    """Aggregate every policy whose target lives in ``dataset_id``."""
    matches = [
        p
        for p in catalog.list_all_row_access_policies()
        if p.project_id == project_id and p.dataset_id == dataset_id
    ]
    matches.sort(
        key=lambda p: (p.table_id, p.policy_id),
    )
    return tuple(matches)


def _rap_as_values(
    rows: tuple[RowAccessPolicyMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    if not rows:
        return _empty_rap_values_subquery()
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    rendered_rows: list[str] = []
    for r in rows:
        tid = _sql_literal(r.table_id)
        policy_name = _sql_literal(r.policy_id)
        grantees_text = ", ".join(r.grantees)
        grantees = _sql_literal(grantees_text)
        predicate = _sql_literal(r.filter_predicate)
        created = _ts_literal(int(r.creation_time.timestamp() * 1000))
        modified = _ts_literal(int(r.last_modified_time.timestamp() * 1000))
        rendered_rows.append(
            f"({pid}, {did}, {tid}, {policy_name}, {grantees}, {predicate}, {created}, {modified})",
        )
    col_list = ", ".join(_RAP_COLUMNS)
    joined = ", ".join(rendered_rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_rap_values_subquery() -> str:
    col_list = ", ".join(_RAP_COLUMNS)
    return (
        "(SELECT * FROM (VALUES (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)) "
        f"AS v({col_list}) WHERE FALSE)"
    )


# ---------------------------------------------------------------------------
# SCHEMATA (G4)
# ---------------------------------------------------------------------------


# BigQuery's published SCHEMATA column order. Position is part of the
# contract — ``SELECT *`` against the view returns columns in this
# order, and fixtures recorded from real BQ assert on it.
_SCHEMATA_COLUMNS: tuple[str, ...] = (
    "catalog_name",
    "schema_name",
    "schema_owner",
    "creation_time",
    "last_modified_time",
    "location",
    "ddl",
    "default_collation_name",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_SCHEMATA_COLUMNS`` 1:1.
_SCHEMATA_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("catalog_name", "STRING"),
    ("schema_name", "STRING"),
    ("schema_owner", "STRING"),
    ("creation_time", "TIMESTAMP"),
    ("last_modified_time", "TIMESTAMP"),
    ("location", "STRING"),
    ("ddl", "STRING"),
    ("default_collation_name", "STRING"),
)


def expand_information_schema_schemata(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.SCHEMATA`` references inline.

    The SCHEMATA view is *project-scoped* on BigQuery — listing every
    dataset in the project. Dataset-scoped references (rare in
    practice; some clients emit them after fully qualifying the
    INFORMATION_SCHEMA path) are interpreted the same way: a project's
    SCHEMATA always contains the full dataset list.
    """
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        return _schemata_as_values(catalog.list_datasets(proj), proj)

    def replace_ds(_match: re.Match[str]) -> str:
        return _schemata_as_values(catalog.list_datasets(project_id), project_id)

    def replace_bare(_match: re.Match[str]) -> str:
        return _schemata_as_values(catalog.list_datasets(project_id), project_id)

    out = _SCHEMATA_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _SCHEMATA_DS.sub(replace_ds, out)
    return _SCHEMATA_BARE.sub(replace_bare, out)


def _schemata_as_values(
    datasets: tuple[DatasetMeta, ...],
    project_id: str,
) -> str:
    if not datasets:
        return _empty_schemata_values_subquery()
    rows: list[str] = []
    pid = _sql_literal(project_id)
    for d in sorted(datasets, key=lambda x: x.dataset_id):
        sid = _sql_literal(d.dataset_id)
        created = _ts_literal(int(d.creation_time.timestamp() * 1000))
        modified = _ts_literal(int(d.last_modified_time.timestamp() * 1000))
        location = _sql_literal(d.location)
        ddl = _sql_literal(f"CREATE SCHEMA `{d.project_id}.{d.dataset_id}`")
        coll = _sql_literal(d.default_collation) if d.default_collation else "NULL"
        rows.append(
            f"({pid}, {sid}, NULL, {created}, {modified}, {location}, {ddl}, {coll})",
        )
    col_list = ", ".join(_SCHEMATA_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_schemata_values_subquery() -> str:
    casts = ", ".join(
        f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _SCHEMATA_COLUMN_TYPES
    )
    return f"(SELECT {casts} WHERE FALSE)"


# ---------------------------------------------------------------------------
# TABLES (G4)
# ---------------------------------------------------------------------------


# Order matches BigQuery's published TABLES schema. Some BQ-only
# columns (``ddl``, ``clone_time``, ``snapshot_time_ms``) are added at
# the tail; the practical-six (catalog/schema/name/type/is_insertable_
# into/is_typed) sit at the front per the public docs.
_TABLES_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "table_type",
    "is_insertable_into",
    "is_typed",
    "creation_time",
    "base_table_catalog",
    "base_table_schema",
    "base_table_name",
    "snapshot_time_ms",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_TABLES_COLUMNS`` 1:1.
_TABLES_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("table_catalog", "STRING"),
    ("table_schema", "STRING"),
    ("table_name", "STRING"),
    ("table_type", "STRING"),
    ("is_insertable_into", "STRING"),
    ("is_typed", "STRING"),
    ("creation_time", "TIMESTAMP"),
    ("base_table_catalog", "STRING"),
    ("base_table_schema", "STRING"),
    ("base_table_name", "STRING"),
    ("snapshot_time_ms", "INTEGER"),
)


# Mapping from our internal :class:`TableType` to BigQuery's
# documented ``table_type`` column value. Our ``"TABLE"`` is BigQuery's
# ``"BASE TABLE"``; everything else passes through unchanged because
# the BQ vocabulary already uses our spelling.
_BQ_TABLE_TYPE_MAP: dict[str, str] = {
    "TABLE": "BASE TABLE",
    "VIEW": "VIEW",
    "MATERIALIZED_VIEW": "MATERIALIZED VIEW",
    "EXTERNAL": "EXTERNAL",
    "SNAPSHOT": "SNAPSHOT",
    "CLONE": "BASE TABLE",
}


def expand_information_schema_tables(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.TABLES`` references inline."""
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        return _tables_as_values(catalog.list_tables(proj, dataset), proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        return _tables_as_values(
            catalog.list_tables(project_id, dataset),
            project_id,
            dataset,
        )

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_tables_values_subquery()

    out = _TABLES_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _TABLES_DS.sub(replace_ds, out)
    return _TABLES_BARE.sub(replace_bare, out)


def _tables_as_values(
    tables: tuple[TableMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    if not tables:
        return _empty_tables_values_subquery()
    rows: list[str] = []
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    for t in sorted(tables, key=lambda x: x.table_id):
        tid = _sql_literal(t.table_id)
        bq_type = _BQ_TABLE_TYPE_MAP.get(t.table_type, t.table_type)
        bq_type_lit = _sql_literal(bq_type)
        is_insertable = _sql_literal("YES" if bq_type == "BASE TABLE" else "NO")
        is_typed = _sql_literal("NO")
        created = _ts_literal(int(t.creation_time.timestamp() * 1000))
        base_cat, base_schema, base_name = _split_base_table(t.base_table)
        snapshot_ms = str(int(t.snapshot_time.timestamp() * 1000)) if t.snapshot_time else "NULL"
        rows.append(
            f"({pid}, {did}, {tid}, {bq_type_lit}, {is_insertable}, "
            f"{is_typed}, {created}, {base_cat}, {base_schema}, {base_name}, {snapshot_ms})",
        )
    col_list = ", ".join(_TABLES_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_tables_values_subquery() -> str:
    casts = ", ".join(f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _TABLES_COLUMN_TYPES)
    return f"(SELECT {casts} WHERE FALSE)"


def _split_base_table(base: str | None) -> tuple[str, str, str]:
    """Split a ``project.dataset.table`` reference into three SQL literals.

    Returns ``("NULL", "NULL", "NULL")`` for absent base table.
    """
    if not base:
        return ("NULL", "NULL", "NULL")
    parts = base.split(".", 2)
    if len(parts) != 3:  # noqa: PLR2004 — three-part reference contract
        return (_sql_literal(base), "NULL", "NULL")
    return tuple(_sql_literal(p) for p in parts)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# COLUMNS (G4)
# ---------------------------------------------------------------------------


_COLUMNS_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "column_name",
    "ordinal_position",
    "is_nullable",
    "data_type",
    "is_generated",
    "generation_expression",
    "is_stored",
    "is_hidden",
    "is_updatable",
    "is_system_defined",
    "is_partitioning_column",
    "clustering_ordinal_position",
    "collation_name",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_COLUMNS_COLUMNS`` 1:1.
_COLUMNS_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("table_catalog", "STRING"),
    ("table_schema", "STRING"),
    ("table_name", "STRING"),
    ("column_name", "STRING"),
    ("ordinal_position", "INTEGER"),
    ("is_nullable", "STRING"),
    ("data_type", "STRING"),
    ("is_generated", "STRING"),
    ("generation_expression", "STRING"),
    ("is_stored", "STRING"),
    ("is_hidden", "STRING"),
    ("is_updatable", "STRING"),
    ("is_system_defined", "STRING"),
    ("is_partitioning_column", "STRING"),
    ("clustering_ordinal_position", "INTEGER"),
    ("collation_name", "STRING"),
)


def expand_information_schema_columns(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.COLUMNS`` references inline."""
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        return _columns_as_values(catalog.list_tables(proj, dataset), proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        return _columns_as_values(
            catalog.list_tables(project_id, dataset),
            project_id,
            dataset,
        )

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_columns_values_subquery()

    out = _COLUMNS_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _COLUMNS_DS.sub(replace_ds, out)
    return _COLUMNS_BARE.sub(replace_bare, out)


def _columns_as_values(
    tables: tuple[TableMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    if not tables:
        return _empty_columns_values_subquery()
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    rows: list[str] = []
    for t in sorted(tables, key=lambda x: x.table_id):
        tid = _sql_literal(t.table_id)
        partition_field = _partition_field(t)
        clustering_fields: dict[str, int] = (
            {f: i + 1 for i, f in enumerate(t.clustering.fields)}
            if t.clustering is not None
            else {}
        )
        for ordinal, field in enumerate(t.schema_.fields, start=1):
            col_name = _sql_literal(field.name)
            is_nullable = _sql_literal("YES" if field.mode != "REQUIRED" else "NO")
            data_type = _sql_literal(_render_data_type(field))
            is_generated = _sql_literal("NEVER")
            generation_expr = "NULL"
            is_stored = _sql_literal("NEVER")
            is_hidden = _sql_literal("NO")
            is_updatable = _sql_literal("YES" if t.table_type == "TABLE" else "NO")
            is_system_defined = _sql_literal("NO")
            is_partitioning = _sql_literal(
                "YES" if partition_field is not None and field.name == partition_field else "NO",
            )
            clust_pos = (
                str(clustering_fields[field.name]) if field.name in clustering_fields else "NULL"
            )
            coll = _sql_literal(field.collation) if field.collation else "NULL"
            rows.append(
                f"({pid}, {did}, {tid}, {col_name}, {ordinal}, {is_nullable}, "
                f"{data_type}, {is_generated}, {generation_expr}, {is_stored}, "
                f"{is_hidden}, {is_updatable}, {is_system_defined}, "
                f"{is_partitioning}, {clust_pos}, {coll})",
            )
    if not rows:
        return _empty_columns_values_subquery()
    col_list = ", ".join(_COLUMNS_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_columns_values_subquery() -> str:
    casts = ", ".join(f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _COLUMNS_COLUMN_TYPES)
    return f"(SELECT {casts} WHERE FALSE)"


def _partition_field(t: TableMeta) -> str | None:
    """Return the partitioning column name, if any."""
    if t.time_partitioning is not None and t.time_partitioning.field:
        return t.time_partitioning.field
    if t.range_partitioning is not None:
        return t.range_partitioning.field
    return None


def _render_data_type(field: TableFieldSchema) -> str:
    """Render a BigQuery-shaped ``data_type`` string for a column.

    Matches BigQuery's documented form:

    * scalars: bare type name (``INT64``, ``STRING``, ``TIMESTAMP``).
    * ``ARRAY<T>`` for repeated fields.
    * ``STRUCT<field1 T1, field2 T2>`` for nested records.
    * ``RANGE<T>`` for RANGE columns (with the inner element type).
    * NUMERIC/BIGNUMERIC carry their precision/scale when set.
    """
    inner = _render_inner_type(field)
    if field.mode == "REPEATED":
        return f"ARRAY<{inner}>"
    return inner


#: Legacy BigQuery type names → GoogleSQL canonical names used in
#: ``INFORMATION_SCHEMA.COLUMNS.data_type``. Real BigQuery accepts the
#: legacy spellings on input but always renders the canonical form in
#: the information-schema view, so we normalise here to match.
_LEGACY_TYPE_ALIASES: dict[str, str] = {
    "INTEGER": "INT64",
    "FLOAT": "FLOAT64",
    "BOOLEAN": "BOOL",
    "RECORD": "STRUCT",
}


def _render_inner_type(field: TableFieldSchema) -> str:
    bq_type = field.type.upper()
    bq_type = _LEGACY_TYPE_ALIASES.get(bq_type, bq_type)
    if bq_type == "STRUCT":
        nested = ", ".join(
            f"{nested_field.name} {_render_data_type(nested_field)}"
            for nested_field in field.fields
        )
        return f"STRUCT<{nested}>"
    if bq_type == "RANGE" and field.range_element_type is not None:
        return f"RANGE<{_render_inner_type(field.range_element_type)}>"
    if bq_type in {"NUMERIC", "BIGNUMERIC"} and field.precision is not None:
        if field.scale is not None:
            return f"{bq_type}({field.precision}, {field.scale})"
        return f"{bq_type}({field.precision})"
    return bq_type


# ---------------------------------------------------------------------------
# TABLE_OPTIONS (G4)
# ---------------------------------------------------------------------------


_TABLE_OPTIONS_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "option_name",
    "option_type",
    "option_value",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_TABLE_OPTIONS_COLUMNS`` 1:1.
_TABLE_OPTIONS_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("table_catalog", "STRING"),
    ("table_schema", "STRING"),
    ("table_name", "STRING"),
    ("option_name", "STRING"),
    ("option_type", "STRING"),
    ("option_value", "STRING"),
)


def expand_information_schema_table_options(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.TABLE_OPTIONS`` references inline.

    Emits one row per (table, option). Currently exposed options:
    ``description``, ``friendly_name``, ``labels``, ``expiration_timestamp``,
    ``require_partition_filter``, ``partition_expiration_days``.
    """
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        return _table_options_as_values(
            catalog.list_tables(proj, dataset),
            proj,
            dataset,
        )

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        return _table_options_as_values(
            catalog.list_tables(project_id, dataset),
            project_id,
            dataset,
        )

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_table_options_values_subquery()

    out = _TABLE_OPTIONS_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _TABLE_OPTIONS_DS.sub(replace_ds, out)
    return _TABLE_OPTIONS_BARE.sub(replace_bare, out)


def _table_options_as_values(
    tables: tuple[TableMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    if not tables:
        return _empty_table_options_values_subquery()
    rows: list[str] = []
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    for t in sorted(tables, key=lambda x: x.table_id):
        tid = _sql_literal(t.table_id)
        for opt_name, opt_type, opt_value in _table_options(t):
            rows.append(
                f"({pid}, {did}, {tid}, {_sql_literal(opt_name)}, "
                f"{_sql_literal(opt_type)}, {opt_value})",
            )
    if not rows:
        return _empty_table_options_values_subquery()
    col_list = ", ".join(_TABLE_OPTIONS_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_table_options_values_subquery() -> str:
    casts = ", ".join(
        f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _TABLE_OPTIONS_COLUMN_TYPES
    )
    return f"(SELECT {casts} WHERE FALSE)"


def _table_options(table: TableMeta) -> list[tuple[str, str, str]]:
    """Yield (option_name, option_type, option_value-SQL-literal) per set option."""
    out: list[tuple[str, str, str]] = []
    if table.description is not None:
        out.append(
            ("description", "STRING", _sql_literal(_quote_for_option(table.description))),
        )
    if table.friendly_name is not None:
        out.append(
            ("friendly_name", "STRING", _sql_literal(_quote_for_option(table.friendly_name))),
        )
    if table.labels:
        rendered_pairs = ", ".join(
            f'("{key}", "{value}")' for key, value in sorted(table.labels.items())
        )
        out.append(
            (
                "labels",
                "ARRAY<STRUCT<STRING, STRING>>",
                _sql_literal(f"[{rendered_pairs}]"),
            ),
        )
    if table.expiration_time is not None:
        ts = table.expiration_time
        out.append(
            (
                "expiration_timestamp",
                "TIMESTAMP",
                _sql_literal(f'TIMESTAMP "{ts.strftime("%Y-%m-%d %H:%M:%S.%f UTC")}"'),
            ),
        )
    if table.time_partitioning is not None:
        if table.time_partitioning.require_partition_filter:
            out.append(("require_partition_filter", "BOOL", _sql_literal("true")))
        if table.time_partitioning.expiration_ms is not None:
            days = table.time_partitioning.expiration_ms / (24 * 60 * 60 * 1000)
            out.append(
                ("partition_expiration_days", "FLOAT64", _sql_literal(f"{days:.6g}")),
            )
    return out


def _quote_for_option(value: str) -> str:
    """Wrap a user-visible option value in double quotes (the BQ SQL form).

    BigQuery's ``OPTIONS(description="foo")`` syntax stores the value as
    a string literal in source form; the INFORMATION_SCHEMA view echoes
    it back verbatim. We escape embedded double quotes with a backslash
    to keep the inline SQL literal parseable downstream.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# VIEWS (G4)
# ---------------------------------------------------------------------------


_VIEWS_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "view_definition",
    "check_option",
    "use_standard_sql",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_VIEWS_COLUMNS`` 1:1.
_VIEWS_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("table_catalog", "STRING"),
    ("table_schema", "STRING"),
    ("table_name", "STRING"),
    ("view_definition", "STRING"),
    ("check_option", "STRING"),
    ("use_standard_sql", "STRING"),
)


def expand_information_schema_views(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.VIEWS`` references inline."""
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        return _views_as_values(catalog.list_views(proj, dataset), proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        return _views_as_values(
            catalog.list_views(project_id, dataset),
            project_id,
            dataset,
        )

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_views_values_subquery()

    out = _VIEWS_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _VIEWS_DS.sub(replace_ds, out)
    return _VIEWS_BARE.sub(replace_bare, out)


def _views_as_values(
    views: tuple[TableMeta, ...],
    project_id: str,
    dataset_id: str,
) -> str:
    if not views:
        return _empty_views_values_subquery()
    rows: list[str] = []
    pid = _sql_literal(project_id)
    did = _sql_literal(dataset_id)
    for v in sorted(views, key=lambda x: x.table_id):
        tid = _sql_literal(v.table_id)
        view_def = _sql_literal(v.view_query or "")
        check_option = "NULL"
        use_std_sql = _sql_literal("YES" if not v.use_legacy_sql else "NO")
        rows.append(
            f"({pid}, {did}, {tid}, {view_def}, {check_option}, {use_std_sql})",
        )
    col_list = ", ".join(_VIEWS_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_views_values_subquery() -> str:
    casts = ", ".join(f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _VIEWS_COLUMN_TYPES)
    return f"(SELECT {casts} WHERE FALSE)"


# ---------------------------------------------------------------------------
# PARTITIONS (G4)
# ---------------------------------------------------------------------------


_PARTITIONS_COLUMNS: tuple[str, ...] = (
    "table_catalog",
    "table_schema",
    "table_name",
    "partition_id",
    "total_rows",
    "total_logical_bytes",
    "last_modified_time",
    "storage_tier",
)


# Per-column BigQuery types for the empty-view CAST(NULL AS <type>) form.
# Order matches ``_PARTITIONS_COLUMNS`` 1:1.
_PARTITIONS_COLUMN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("table_catalog", "STRING"),
    ("table_schema", "STRING"),
    ("table_name", "STRING"),
    ("partition_id", "STRING"),
    ("total_rows", "INTEGER"),
    ("total_logical_bytes", "INTEGER"),
    ("last_modified_time", "TIMESTAMP"),
    ("storage_tier", "STRING"),
)


def expand_information_schema_partitions(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Replace ``INFORMATION_SCHEMA.PARTITIONS`` references inline.

    Partition rows are derived on the fly by walking every table in
    the dataset and asking the catalog for per-table partitions. The
    catalog implementations query DuckDB directly when an engine is
    wired (the production path); in pure unit-test paths without a
    live engine the per-table list is empty and the view collapses to
    the empty-VALUES sentinel.
    """
    if "INFORMATION_SCHEMA" not in bq_sql.upper():
        return bq_sql

    def replace_project_ds(match: re.Match[str]) -> str:
        proj = match.group("project")
        dataset = match.group("dataset")
        return _partitions_as_values(catalog, proj, dataset)

    def replace_ds(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        return _partitions_as_values(catalog, project_id, dataset)

    def replace_bare(_match: re.Match[str]) -> str:
        return _empty_partitions_values_subquery()

    out = _PARTITIONS_PROJECT_DS.sub(replace_project_ds, bq_sql)
    out = _PARTITIONS_DS.sub(replace_ds, out)
    return _PARTITIONS_BARE.sub(replace_bare, out)


def _partitions_as_values(
    catalog: CatalogRepository,
    project_id: str,
    dataset_id: str,
) -> str:
    tables = catalog.list_tables(project_id, dataset_id)
    all_partitions: list[PartitionMeta] = []
    for t in sorted(tables, key=lambda x: x.table_id):
        if t.table_type not in {"TABLE", "SNAPSHOT", "CLONE"}:
            continue
        all_partitions.extend(catalog.list_partitions(project_id, dataset_id, t.table_id))
    if not all_partitions:
        return _empty_partitions_values_subquery()
    rows: list[str] = [
        f"({_sql_literal(p.table_catalog)}, {_sql_literal(p.table_schema)}, "
        f"{_sql_literal(p.table_name)}, {_sql_literal(p.partition_id)}, "
        f"{p.total_rows}, {p.total_logical_bytes}, "
        f"{_ts_literal(int(p.last_modified_time.timestamp() * 1000))}, "
        f"{_sql_literal(p.storage_tier)})"
        for p in all_partitions
    ]
    col_list = ", ".join(_PARTITIONS_COLUMNS)
    joined = ", ".join(rows)
    return f"(SELECT * FROM (VALUES {joined}) AS v({col_list}))"


def _empty_partitions_values_subquery() -> str:
    casts = ", ".join(
        f"CAST(NULL AS {bq_type}) AS {col}" for col, bq_type in _PARTITIONS_COLUMN_TYPES
    )
    return f"(SELECT {casts} WHERE FALSE)"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _ts_literal(ms: int) -> str:
    """Render *ms* milliseconds-since-epoch as a BigQuery ``TIMESTAMP`` literal.

    We emit the ISO 8601 form (``TIMESTAMP '...' UTC``) rather than the
    functional ``TIMESTAMP_MILLIS(N)`` call. Both are semantically
    equivalent for BigQuery, but the literal form survives the
    ``datetime_helpers`` pre-translator's parse + serialize round-trip
    unchanged — the SQLGlot BigQuery output drops the named-column
    alias from a ``VALUES (...) AS v(col_list)`` clause when it
    transpiles to ``UNNEST([STRUCT(... AS _c0, ...)])``, and
    ``TIMESTAMP_MILLIS`` is the one builtin in this rewriter that
    triggers the round-trip.
    """
    from datetime import UTC, datetime, timedelta

    ts = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
    return f"TIMESTAMP '{ts.strftime('%Y-%m-%d %H:%M:%S.%f')} UTC'"


__all__ = [
    "expand_information_schema",
    "expand_information_schema_columns",
    "expand_information_schema_materialized_views",
    "expand_information_schema_partitions",
    "expand_information_schema_routines",
    "expand_information_schema_row_access_policies",
    "expand_information_schema_schemata",
    "expand_information_schema_table_options",
    "expand_information_schema_tables",
    "expand_information_schema_views",
]
