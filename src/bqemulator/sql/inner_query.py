"""Canonical inner-query rewrite and translation pipeline.

A BigQuery statement reaches DuckDB through one rewrite chain regardless
of whether it runs as a standalone single-statement job
(:func:`bqemulator.jobs.executor._run_single_sql`) or inside a script
(:class:`bqemulator.scripting.interpreter.ScriptInterpreter`). This
module is that single chain so the two execution paths cannot drift:
materialized-view refresh, ``FOR SYSTEM_TIME AS OF`` time-travel
resolution, row-access enforcement, ``INFORMATION_SCHEMA`` expansion,
``UNNEST`` offset rewriting, wildcard-table expansion, schema-annotated
BigQuery to DuckDB translation, and table-reference qualification.

Callers layer their own concerns on top of the DuckDB SQL it returns:
parameter binding (named query parameters for standalone jobs, the
positional placeholders the scripting interpreter emits for ``@var``
substitution and ``USING`` values), execution (``fetch_arrow`` for
row-producing statements, ``execute`` for dynamic DDL/DML), and
error-shaping. Scripting-specific pre-rewrites (``_rewrite_temp_calls``,
``_rewrite_vars_to_params``) run in the interpreter before the SQL is
handed to :func:`rewrite_and_translate_statement`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.domain.result import Err, Ok
from bqemulator.sql.catalog_schema import build_catalog_schema
from bqemulator.sql.rewriter.information_schema import expand_information_schema
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access
from bqemulator.sql.rewriter.unnest_offset import rewrite_unnest_offset
from bqemulator.sql.rewriter.wildcard_expander import expand_wildcard_tables
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.time_travel import rewrite_for_system_time

if TYPE_CHECKING:
    from bqemulator.api.dependencies import AppContext
    from bqemulator.row_access.identity import CallerIdentity
    from bqemulator.sql.translator import SQLTranslator


async def refresh_dependent_mvs(project_id: str, bq_sql: str, ctx: AppContext) -> None:
    """Refresh any materialized view this query reads, if stale.

    Walks the BigQuery AST, collects every table reference, and asks the
    MV manager to ``refresh_if_stale``. No-op when the query touches no
    materialized view.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — fall through so later layers error cleanly
        return

    manager = MaterializedViewManager(ctx)
    for table_node in tree.find_all(exp.Table):
        if isinstance(table_node.this, exp.Anonymous):
            continue
        name = table_node.name
        dataset = table_node.db
        if not name or not dataset:
            continue
        proj = table_node.catalog or project_id
        meta = ctx.catalog.get_table(proj, dataset, name)
        if meta is None or meta.table_type != "MATERIALIZED_VIEW":
            continue
        await manager.refresh_if_stale(proj, dataset, name)


async def rewrite_and_translate_statement(
    bq_sql: str,
    *,
    project_id: str,
    ctx: AppContext,
    caller: CallerIdentity,
    translator: SQLTranslator,
) -> str:
    """Run the canonical rewrite chain on one statement, returning DuckDB SQL.

    Handles any single BigQuery statement, not only ``SELECT``: the
    standalone job path and the scripting interpreter (including
    ``EXECUTE IMMEDIATE`` dynamic DDL/DML) both route through here. The
    materialized-view refresh and time-travel passes are no-ops for a
    statement that reads no table or carries no ``FOR SYSTEM_TIME``
    clause, so applying the full chain uniformly is safe.

    Applies, in order: materialized-view refresh, ``FOR SYSTEM_TIME AS
    OF`` time-travel resolution, row-access enforcement,
    ``INFORMATION_SCHEMA`` expansion, ``UNNEST`` offset rewriting,
    wildcard-table expansion, schema-annotated BigQuery to DuckDB
    translation, and table-reference qualification.

    Raises the translator's error on a failed translation. Parameter
    binding and execution (and their runtime error-shaping) are the
    caller's responsibility.
    """
    # Refresh any stale materialized views this statement reads.
    await refresh_dependent_mvs(project_id, bq_sql, ctx)
    # Resolve FOR SYSTEM_TIME AS OF before the translator runs.
    bq_sql = rewrite_for_system_time(bq_sql, project_id, ctx.snapshots, ctx.engine)
    # Enforce row access policies before any other rewrite.
    bq_sql = rewrite_for_row_access(
        bq_sql,
        project_id=project_id,
        caller=caller,
        catalog=ctx.catalog,
    )
    bq_sql = expand_information_schema(bq_sql, project_id, ctx.catalog)
    bq_sql = rewrite_unnest_offset(bq_sql)
    bq_sql = expand_wildcard_tables(bq_sql, project_id, ctx.catalog)
    # ADR 0023 §1.B: build a per-table schema snapshot so the translator's
    # ``annotate_types`` pass can resolve column types — the
    # ``AvgDecimalRule`` consults the annotated operand type to decide
    # whether to wrap ``AVG`` in a DECIMAL cast.
    schema_dict = build_catalog_schema(bq_sql, project_id=project_id, catalog=ctx.catalog)
    match translator.translate(bq_sql, schema=schema_dict or None, caller=caller):
        case Err(error):
            raise error
        case Ok(duckdb_sql):
            pass
    return rewrite_table_refs(duckdb_sql, project_id)


__all__ = ["refresh_dependent_mvs", "rewrite_and_translate_statement"]
