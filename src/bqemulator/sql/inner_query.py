"""Canonical inner-query rewrite and translation pipeline.

A BigQuery statement reaches DuckDB through one rewrite chain regardless
of whether it runs as a standalone single-statement job
(:func:`bqemulator.jobs.executor._run_single_sql`) or inside a script
(:class:`bqemulator.scripting.interpreter.ScriptInterpreter`). This
module is the single shared chain both execution paths run, so their
rewrite and translation behaviour stays identical: materialized-view
refresh, ``FOR SYSTEM_TIME AS OF`` time-travel resolution, row-access
enforcement, ``INFORMATION_SCHEMA`` expansion, ``UNNEST`` offset
rewriting, wildcard-table expansion, and schema-annotated BigQuery to
DuckDB translation.

Callers layer their own concerns on top of the translated SQL it
returns: table-reference qualification (``rewrite_table_refs``),
parameter binding (named query parameters for standalone jobs, the
positional placeholders the scripting interpreter emits for ``@var``
substitution and ``USING`` values), execution (``fetch_arrow`` for
row-producing statements, ``execute`` for dynamic DDL/DML), and
error-shaping. Qualification, binding, and execution stay at the call
site so each caller wraps them in its own ``try``. The standalone path
reshapes a malformed-id ``ValidationError`` from qualification via
``translate_runtime_error``, whereas an unsupported-feature error from
the translation step inside this helper must surface unwrapped (as a
``501``), so it is raised before the caller's ``try``.
Scripting-specific pre-rewrites (``_rewrite_temp_calls``,
``_rewrite_vars_to_params``) run in the interpreter before the SQL is
handed to :func:`rewrite_and_translate_statement`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.domain.result import Err, Ok
from bqemulator.sql.catalog_schema import build_catalog_schema
from bqemulator.sql.rewriter.information_schema import expand_information_schema
from bqemulator.sql.rewriter.ml_predict import rewrite_ml_predict
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access
from bqemulator.sql.rewriter.unnest_offset import rewrite_unnest_offset
from bqemulator.sql.rewriter.wildcard_expander import expand_wildcard_tables
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.time_travel import rewrite_for_system_time

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bqemulator.api.dependencies import AppContext
    from bqemulator.row_access.identity import CallerIdentity
    from bqemulator.sql.translator import SQLTranslator


async def refresh_dependent_mvs(project_id: str, bq_sql: str, ctx: AppContext) -> None:
    """Refresh any materialized view this query reads, if stale.

    Walks the BigQuery AST, collects every materialized view referenced,
    and asks the MV manager to ``refresh_if_stale``. No-op when the query
    touches no materialized view.
    """
    # When the catalog holds no materialized views at all, refresh is a
    # guaranteed no-op, so skip parsing entirely. This keeps the hot path
    # cheap for the common no-MV case, notably for scripted statements,
    # which route every statement through this chain.
    if not ctx.catalog.list_all_materialized_views():
        return

    manager = MaterializedViewManager(ctx)
    for proj, dataset, name in _referenced_materialized_views(bq_sql, project_id, ctx):
        await manager.refresh_if_stale(proj, dataset, name)


def _referenced_materialized_views(
    bq_sql: str,
    project_id: str,
    ctx: AppContext,
) -> Iterator[tuple[str, str, str]]:
    """Yield each distinct materialized view referenced by ``bq_sql``.

    Each ``(project, dataset, table)`` is yielded at most once, so a view
    referenced more than once (self-join, repeated subquery) is refreshed
    a single time. Unparseable SQL yields nothing.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001  (parse failure falls through to later layers)
        return

    seen: set[tuple[str, str, str]] = set()
    for table_node in tree.find_all(exp.Table):
        if isinstance(table_node.this, exp.Anonymous):
            continue
        name = table_node.name
        dataset = table_node.db
        if not name or not dataset:
            continue
        ref = (table_node.catalog or project_id, dataset, name)
        if ref in seen:
            continue
        seen.add(ref)
        meta = ctx.catalog.get_table(*ref)
        if meta is None or meta.table_type != "MATERIALIZED_VIEW":
            continue
        yield ref


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
    wildcard-table expansion, and schema-annotated BigQuery to DuckDB
    translation. Returns the translated DuckDB SQL.

    Table-reference qualification (``rewrite_table_refs``), parameter
    binding, and execution are the caller's responsibility, so the
    caller's ``try`` can shape their failures. A failed translation is
    raised here, before the caller's ``try``, so an unsupported-feature
    error reaches the wire unwrapped as a ``501`` rather than being
    reshaped into a generic ``invalidQuery``.
    """
    # Refresh any stale materialized views this statement reads.
    await refresh_dependent_mvs(project_id, bq_sql, ctx)
    # Rewrite ML.PREDICT into a passthrough-plus-prediction subquery before
    # the remaining passes, so its input query's tables flow through
    # time-travel, row-access, INFORMATION_SCHEMA, and wildcard expansion
    # like any other subquery (ADR 0047 / RFC 0002).
    bq_sql = rewrite_ml_predict(bq_sql, project_id=project_id, catalog=ctx.catalog)
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
    # ``annotate_types`` pass can resolve column types; the
    # ``AvgDecimalRule`` consults the annotated operand type to decide
    # whether to wrap ``AVG`` in a DECIMAL cast.
    schema_dict = build_catalog_schema(bq_sql, project_id=project_id, catalog=ctx.catalog)
    match translator.translate(bq_sql, schema=schema_dict or None, caller=caller):
        case Err(error):
            raise error
        case Ok(duckdb_sql):
            pass
    return duckdb_sql


__all__ = ["refresh_dependent_mvs", "rewrite_and_translate_statement"]
