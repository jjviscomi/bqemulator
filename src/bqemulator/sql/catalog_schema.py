"""Catalog-derived schema snapshots for SQLGlot's type annotation pass.

The translator's :class:`AvgDecimalRule` (ADR 0023 §1.B) only fires
when SQLGlot's ``annotate_types`` can resolve the AVG operand's
declared type. That resolution requires a schema dict shaped like
``{table_name: {column_name: duckdb_type, ...}, ...}``.

This module walks the BigQuery-side AST, finds every table reference,
looks each up in the catalog, and emits the dict. Unresolvable refs
(CTEs, sub-query aliases, missing tables) are skipped — the resulting
dict carries only what the translator can usefully annotate.

The helper is intentionally tolerant: SQLGlot parse failures and
catalog lookup failures both surface as an empty dict so the
translator falls back to its legacy (un-annotated) behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.domain.errors import NotFoundError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.type_map import bq_to_duckdb

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.repository import CatalogRepository

_log = get_logger(__name__)


def build_catalog_schema(
    bq_sql: str,
    *,
    project_id: str,
    catalog: CatalogRepository,
) -> dict[str, dict[str, str]]:
    """Return the per-table column type dict for tables referenced in *bq_sql*.

    Keys are the unqualified table-id; values are
    ``{column_name: duckdb_type}`` for the columns declared in the
    catalog. SQLGlot's annotator looks up a column via its bare table
    name (the alias-or-table portion of a ``"db"."schema"."table"``
    reference), so the flat single-level keying is both sufficient and
    matches the BigQuery convention of using the trailing identifier
    as the column qualifier.

    On parse failure or any catalog error, returns an empty dict — the
    translator then runs without operand type annotation and the
    type-aware rules skip cleanly.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return {}

    schema: dict[str, dict[str, str]] = {}
    for table in tree.find_all(exp.Table):
        resolved = _resolve_table_ref(table, project_id=project_id)
        if resolved is None:
            continue
        table_project, dataset_id, table_id = resolved
        try:
            meta = catalog.get_table(table_project, dataset_id, table_id)
        except NotFoundError:
            continue
        if meta is None:
            continue
        columns: dict[str, str] = {}
        for field in meta.schema_.fields:
            duckdb_type = _safe_bq_to_duckdb(field.type)
            if duckdb_type is None:
                continue
            columns[field.name] = duckdb_type
        if columns:
            schema[table_id] = columns
    return schema


def _resolve_table_ref(
    table: exp.Table,
    *,
    project_id: str,
) -> tuple[str, str, str] | None:
    """Resolve a SQLGlot ``Table`` node to ``(project, dataset, table)``.

    BigQuery accepts three reference shapes — bare ``table``,
    ``dataset.table``, and ``project.dataset.table`` (optionally
    backticked). SQLGlot puts the rightmost segment in ``name``, the
    middle in ``db``, and the leftmost in ``catalog``; we map them to
    the catalog repository's ``project_id`` / ``dataset_id`` /
    ``table_id`` arguments. Bare table refs are skipped — without a
    dataset hint the catalog lookup is ambiguous and the conformance
    fixtures we exercise always qualify with at least
    ``dataset.table``.
    """
    table_name = table.name
    if not table_name:
        return None
    dataset_id = table.db or ""
    explicit_project = table.catalog or ""
    if not dataset_id:
        return None
    table_project = explicit_project or project_id
    return table_project, dataset_id, table_name


_BQ_TYPE_ALIASES: dict[str, str] = {
    # Catalog metadata uses BigQuery wire-format names (``INTEGER`` /
    # ``FLOAT`` / ``BOOLEAN`` / ``RECORD``) alongside the GoogleSQL
    # spellings (``INT64`` / ``FLOAT64`` / ``BOOL`` / ``STRUCT``). The
    # type map only accepts the GoogleSQL spellings, so we collapse
    # the alias before dispatch.
    "INTEGER": "INT64",
    "FLOAT": "FLOAT64",
    "BOOLEAN": "BOOL",
    "RECORD": "STRUCT",
}


def _safe_bq_to_duckdb(bq_type: str) -> str | None:
    """Translate a BigQuery type name; return ``None`` on unmappable types.

    Normalises catalog-stored wire-format aliases (``INTEGER``,
    ``FLOAT``, ``BOOLEAN``, ``RECORD``) into the GoogleSQL spellings
    the type map accepts. The translator's annotate-types pass treats
    missing entries as ``UNKNOWN``; mapping failures (custom catalog
    types, malformed entries) flow through as ``None`` so the
    annotator falls back to its defaults for the affected column.
    """
    upper = bq_type.strip().upper()
    normalised = _BQ_TYPE_ALIASES.get(upper, bq_type)
    try:
        return bq_to_duckdb(normalised)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["build_catalog_schema"]
