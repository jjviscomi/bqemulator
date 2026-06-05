"""Job executor — dispatches job commands asynchronously.

The executor manages a bounded semaphore to cap concurrent jobs and
routes each job type to its command implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pyarrow as pa

from bqemulator.catalog.ddl_sync import (
    assert_drop_schema_allowed,
    sync_created_schema,
    sync_created_table,
    sync_created_view,
    sync_dropped_object,
)
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import JobMeta
from bqemulator.domain.errors import (
    DomainError,
    InvalidQueryError,
    ResourceRef,
    UnsupportedFeatureError,
    resource_not_found,
)
from bqemulator.domain.events import TableDataChanged
from bqemulator.domain.result import Err, Ok
from bqemulator.jobs.avro_reader import (
    is_decimal_logical_avro,
    read_avro_to_arrow,
)
from bqemulator.jobs.ddl_result import (
    ddl_operation_for,
    ddl_result_schema_fields,
    resolve_ddl_operation,
)
from bqemulator.jobs.error_mapper import translate_runtime_error
from bqemulator.jobs.orc_reader import read_orc_to_arrow
from bqemulator.jobs.routine_ddl import (
    classify_create_routine,
    detect_drop_routine,
    resolve_create_routine_operation,
    run_drop_routine,
)
from bqemulator.observability.logging_ import get_logger
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.scripting.ast import SqlStmt
from bqemulator.scripting.interpreter import ScriptInterpreter
from bqemulator.scripting.parser import parse_script
from bqemulator.sql.catalog_schema import build_catalog_schema
from bqemulator.sql.parameters import bind_parameters
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.versioning.ddl import (
    VersioningDDLRouter,
    execute_versioning_ddl,
    is_versioning_ddl,
)
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.time_travel import rewrite_for_system_time

if TYPE_CHECKING:
    from bqemulator.api.dependencies import AppContext

_log = get_logger(__name__)
_translator = SQLTranslator()

# Module-level result storage shared across the executor + the
# ``jobs.getQueryResults`` route. A persistent registry would let
# results survive process restarts; for the emulator's intended
# dev / CI / offline-replica scope, in-process is sufficient.
JOB_RESULTS: dict[str, pa.Table] = {}
JOB_SCHEMAS: dict[str, list[dict[str, Any]]] = {}


def _is_any_list_arrow_type(arrow_type: pa.DataType) -> bool:
    """True for ``list`` and ``large_list`` Arrow types (single test, REPEATED mode)."""
    return pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type)


def _is_any_float_arrow_type(arrow_type: pa.DataType) -> bool:
    """True for ``float32`` and ``float64`` Arrow types (both map to BQ ``FLOAT``)."""
    return pa.types.is_float64(arrow_type) or pa.types.is_float32(arrow_type)


def _is_any_string_arrow_type(arrow_type: pa.DataType) -> bool:
    """True for ``string`` and ``large_string`` Arrow types."""
    return pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type)


def _list_element_bq_type(arrow_type: pa.DataType) -> str:
    """Recurse through a LIST type to return the BigQuery type of the element."""
    return _arrow_type_to_bq_type(arrow_type.value_type)


def _timestamp_bq_type(arrow_type: pa.DataType) -> str:
    """``timestamp(tz)`` → ``TIMESTAMP``; ``timestamp(no tz)`` → ``DATETIME``."""
    return "TIMESTAMP" if arrow_type.tz else "DATETIME"


def _decimal_bq_type(arrow_type: pa.DataType) -> str:
    """ADR 0023 §1.B: scale > 9 → ``BIGNUMERIC``; otherwise ``NUMERIC``.

    BigQuery's NUMERIC has fixed scale 9 and BIGNUMERIC carries scale up
    to 38. Any DECIMAL whose scale exceeds 9 originated from a
    BIGNUMERIC literal / column / ``bqemu_to_bignumeric`` marker —
    surface it as BIGNUMERIC on the wire so the schema matches
    BigQuery's recorded baseline.
    """
    scale = getattr(arrow_type, "scale", 0) or 0
    return "BIGNUMERIC" if scale > 9 else "NUMERIC"  # noqa: PLR2004 — BigQuery NUMERIC scale boundary


#: Ordered Arrow-type → BigQuery-type dispatch rules. Each entry is
#: ``(predicate, mapper)`` where ``mapper`` is either a constant ``str``
#: (the BigQuery type name) or a :class:`Callable` that takes the Arrow
#: type and returns the BigQuery type. The first matching predicate
#: wins; unmatched types fall back to ``STRING``.
#:
#: Ordering matters:
#:
#: * LIST / large_list must come first because they recurse on
#:   ``value_type`` — without that the inner type would never be
#:   exercised by the trailing rules.
#: * ADR 0023 §1.B: DuckDB's ``SIGN(INT)`` returns ``TINYINT`` (Arrow
#:   ``int8``); the catch-all ``is_integer`` rule covers those too.
#: * ADR 0023 §1.G: ``MonthDayNano`` Arrow interval surfaces as
#:   ``INTERVAL`` so DuckDB's INTERVAL columns don't slip through to the
#:   STRING fallback (the value renderer already produces the canonical
#:   ``Y-M D H:M:S`` string for the body).
_ARROW_TO_BQ_RULES: tuple[
    tuple[Callable[[pa.DataType], bool], str | Callable[[pa.DataType], str]],
    ...,
] = (
    (_is_any_list_arrow_type, _list_element_bq_type),
    (pa.types.is_integer, "INTEGER"),
    (_is_any_float_arrow_type, "FLOAT"),
    (pa.types.is_boolean, "BOOLEAN"),
    (_is_any_string_arrow_type, "STRING"),
    (pa.types.is_timestamp, _timestamp_bq_type),
    (pa.types.is_date, "DATE"),
    (pa.types.is_time, "TIME"),
    (pa.types.is_interval, "INTERVAL"),
    (pa.types.is_decimal, _decimal_bq_type),
    (pa.types.is_binary, "BYTES"),
    (pa.types.is_struct, "RECORD"),
)


def _arrow_type_to_bq_type(arrow_type: pa.DataType) -> str:
    """Map a pyarrow scalar type to a BigQuery type name for response schemas.

    For LIST types, returns the BigQuery type of the *element*. The
    REPEATED mode is recorded separately by
    :func:`_arrow_field_to_schema_entry` so the wire format matches
    BigQuery's ``{type: <elem>, mode: REPEATED}`` shape.

    Dispatch order is defined by :data:`_ARROW_TO_BQ_RULES` — see its
    docstring for the ordering invariants. Unmatched types fall back
    to ``STRING``.
    """
    for predicate, mapper in _ARROW_TO_BQ_RULES:
        if predicate(arrow_type):
            return mapper(arrow_type) if callable(mapper) else mapper
    return "STRING"


def _arrow_field_to_schema_entry(field: pa.Field) -> dict[str, Any]:
    """Build a single BigQuery REST schema entry from an Arrow field.

    REPEATED mode is derived from list/large-list Arrow types. Struct
    types (and struct-of-list / list-of-struct) recurse so nested
    fields carry their own type / mode / fields entries.

    When a field carries a ``bqemu.duckdb_type`` metadata entry (set by
    :func:`bqemulator.storage.engine._annotate_with_duckdb_types`),
    that DuckDB-side type overrides the Arrow-derived mapping for
    types that can't round-trip through Arrow without losing fidelity —
    most notably ``JSON``, where DuckDB flattens to a ``string`` Arrow
    type but BigQuery requires ``type: "JSON"`` on the wire.

    ADR 0023 §1.G also threads the DuckDB metadata through for the
    BigQuery ``RANGE<T>`` type: a column whose DuckDB type matches the
    canonical ``STRUCT("start" T, "end" T)`` (optionally repeated)
    surfaces with ``type=RANGE`` plus a ``rangeElementType: {type: T}``
    sub-field, never the underlying ``RECORD`` shape.
    """
    arrow_type = field.type
    range_entry = _maybe_range_schema_entry(field)
    if range_entry is not None:
        return range_entry
    mode = "NULLABLE"
    inner_type = arrow_type
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        mode = "REPEATED"
        inner_type = arrow_type.value_type
    entry: dict[str, Any] = {
        "name": field.name,
        "type": _resolve_bq_type(field, inner_type),
        "mode": mode,
    }
    if pa.types.is_struct(inner_type):
        entry["fields"] = [
            _arrow_field_to_schema_entry(inner_type.field(i)) for i in range(inner_type.num_fields)
        ]
    return entry


def _maybe_range_schema_entry(field: pa.Field) -> dict[str, Any] | None:
    """Return a RANGE-typed BigQuery schema entry, if *field* warrants one.

    Reads ``bqemu.duckdb_type`` metadata and dispatches through
    :func:`bqemulator.types.range_type.detect_range_element`. A
    positive match returns a ``{type: "RANGE", mode, rangeElementType}``
    entry (REPEATED mode iff the DuckDB type is a list of the
    RANGE-shaped struct). Returns ``None`` when no metadata is present
    or the shape is not RANGE — the caller falls back to the normal
    Arrow-derived mapping.
    """
    from bqemulator.types.range_type import detect_range_element

    metadata = field.metadata or {}
    raw = metadata.get(b"bqemu.duckdb_type")
    if raw is None:
        return None
    duckdb_type = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    detection = detect_range_element(duckdb_type)
    if detection is None:
        return None
    bq_elem, is_repeated = detection
    return {
        "name": field.name,
        "type": "RANGE",
        "mode": "REPEATED" if is_repeated else "NULLABLE",
        "rangeElementType": {"type": bq_elem},
    }


def _resolve_bq_type(field: pa.Field, inner_type: pa.DataType) -> str:
    """Return the BigQuery type for *field*, honouring DuckDB-side hints.

    Reads the field's ``bqemu.duckdb_type`` metadata (set by
    :func:`bqemulator.storage.engine._annotate_with_duckdb_types`). The
    DuckDB type wins for cases where Arrow loses fidelity:

    * ``JSON`` — Arrow has no first-class JSON type so it flattens to a
      string; the metadata recovers the BigQuery wire-format type.
    * ``HUGEINT`` (ADR 0023 §1.B) — DuckDB's ``SUM(BIGINT)`` and
      ``COUNT_IF`` aggregates promote to ``HUGEINT``, which Arrow
      encodes as ``decimal128(38, 0)``. Without the override the column
      surfaces as NUMERIC even though BigQuery returns INTEGER.

    Every other Arrow-derived mapping flows through
    :func:`_arrow_type_to_bq_type` unchanged.
    """
    metadata = field.metadata or {}
    duckdb_type = metadata.get(b"bqemu.duckdb_type")
    if duckdb_type is not None:
        decoded = duckdb_type.decode("utf-8").upper()
        if decoded == "JSON":
            return "JSON"
        if decoded == "HUGEINT":
            return "INTEGER"
    return _arrow_type_to_bq_type(inner_type)


def build_response_schema(arrow_schema: pa.Schema) -> list[dict[str, Any]]:
    """Build a BigQuery REST schema from an Arrow schema.

    REPEATED columns (Arrow list types) emit
    ``{type: <element>, mode: "REPEATED"}`` — matching BigQuery's wire
    format. STRUCT columns (and arrays of STRUCT) recurse to populate
    the nested ``fields`` array.

    Duplicate column names are deduplicated with a ``_<n>`` suffix
    (``_TABLE_SUFFIX``, ``_TABLE_SUFFIX_1`` …) to match BigQuery's
    wire-format guarantee that schema field names are unique. DuckDB
    leaves duplicates in place after a self-join projects the same
    column from both sides; the conformance corpus's
    ``wildcard_join_self`` fixture relied on the dedup behaviour.
    """
    entries = [
        _arrow_field_to_schema_entry(arrow_schema.field(i)) for i in range(len(arrow_schema))
    ]
    seen: dict[str, int] = {}
    for entry in entries:
        original = entry["name"]
        count = seen.get(original, 0)
        if count:
            entry["name"] = f"{original}_{count}"
        seen[original] = count + 1
    return entries


async def execute_query_job(
    project_id: str,
    job_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    ctx: AppContext,
    *,
    use_cache: bool = True,  # noqa: ARG001 — reserved for future cache integration
    caller: CallerIdentity | None = None,
) -> JobMeta:
    """Execute a query job and store the result.

    Returns the completed JobMeta. Multi-statement scripts and scripts
    containing any control-flow construct (DECLARE/IF/WHILE/LOOP/FOR/
    BEGIN/EXECUTE IMMEDIATE/CALL/RETURN/RAISE/CREATE FUNCTION/CREATE
    PROCEDURE) run through the scripting interpreter. Single-statement
    SQL follows the legacy fast path with BigQuery query-parameter
    binding.

    ``caller`` is the resolved identity used by the row-access
    rewriter. The default falls back to the anonymous identity (which
    matches no user-defined grantee), so legacy callers that haven't
    been updated keep working.
    """
    now = ctx.clock.now()
    script = _parse_script_or_raise(bq_sql)
    is_scripted = len(script.statements) != 1 or not isinstance(
        script.statements[0],
        SqlStmt,
    )
    script_statement_count = len(script.statements)

    # Classify the statement type up front so the response populates
    # ``statistics.query.statementType`` consistently across the
    # versioning-DDL fast path, the scripting path, and the legacy
    # single-SQL path. Best-effort: unparseable input returns ``""``
    # and the field is omitted.
    statement_type = "SCRIPT" if is_scripted else classify_statement_type(bq_sql)
    ddl_operation: str | None = None

    if is_scripted:
        # A single CREATE FUNCTION / CREATE TABLE FUNCTION routes through
        # the scripting interpreter (which registers the routine) but
        # BigQuery reports it as ``CREATE_FUNCTION`` / ``CREATE_TABLE_FUNCTION``,
        # not ``SCRIPT`` (pinned by ``routines_scripting/routine_ddl_*``).
        # CREATE PROCEDURE stays ``SCRIPT`` — that matches real BigQuery,
        # so ``classify_create_routine`` returns ``""`` for it. The
        # operation is resolved here, before the interpreter registers
        # the routine, so ``OR REPLACE`` over an existing routine reports
        # ``REPLACE``.
        routine_statement_type = classify_create_routine(script)
        if routine_statement_type:
            statement_type = routine_statement_type
            ddl_operation = resolve_create_routine_operation(script, project_id, ctx)
    elif statement_type == "EXPORT_DATA":
        # EXPORT DATA runs the inner SELECT through the standard query
        # pipeline (so row-access / MV / wildcard rewrites all apply) and
        # then writes the result to Cloud Storage. It returns its own
        # closing JobMeta, short-circuiting the SELECT/DML/DDL flow below.
        return await _execute_export_data_job(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            query_params=query_params,
            now=now,
            ctx=ctx,
            caller=caller,
        )
    else:
        early_exit = await _run_query_fast_paths(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            statement_type=statement_type,
            now=now,
            ctx=ctx,
        )
        if early_exit is not None:
            return early_exit

    # Refuse DML against immutable table types (SNAPSHOT,
    # MATERIALIZED_VIEW). BigQuery treats these as read-only.
    _reject_dml_on_immutable(project_id, bq_sql, ctx)

    # Single-statement pre-execution guard + ``ddlOperationPerformed``
    # resolution (the scripted routine case was already resolved above).
    if not is_scripted:
        ddl_operation = _guard_and_resolve_single_ddl(bq_sql, statement_type, project_id, ctx)

    effective_caller = caller or CallerIdentity(
        principal="user:anonymous@bqemulator.local",
        is_authenticated=False,
    )
    arrow_table = await _run_query_body(
        project_id=project_id,
        bq_sql=bq_sql,
        query_params=query_params,
        ctx=ctx,
        is_scripted=is_scripted,
        effective_caller=effective_caller,
    )

    # Capture snapshots for tables modified by this statement, and
    # propagate TableDataChanged so dependent MVs flip stale.
    await _capture_dml_snapshots(project_id, bq_sql, ctx)

    arrow_table, num_dml_affected_rows, ddl_schema_fields = _finalize_statement_result(
        arrow_table,
        statement_type,
        bq_sql=bq_sql,
        project_id=project_id,
        ctx=ctx,
    )
    JOB_RESULTS[job_id] = arrow_table
    JOB_SCHEMAS[job_id] = (
        ddl_schema_fields
        if ddl_schema_fields is not None
        else build_response_schema(arrow_table.schema)
    )
    ctx.metrics.sql_translation_total.labels(outcome="ok").inc()

    statistics = _build_query_statistics(
        total_rows=arrow_table.num_rows,
        statement_type=statement_type,
        num_dml_affected_rows=num_dml_affected_rows,
        ddl_operation=ddl_operation,
    )
    if is_scripted:
        statistics["scriptStatistics"] = {
            "statementCount": str(script_statement_count),
            "evaluationKind": "STATEMENT",
        }

    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="QUERY",
        state="DONE",
        configuration=_build_query_configuration(bq_sql, project_id, job_id),
        statistics=statistics,
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


def _guard_and_resolve_single_ddl(
    bq_sql: str,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> str | None:
    """Pre-execution guard + ``ddlOperationPerformed`` for a single SQL statement.

    Runs BEFORE the statement reaches DuckDB. Rejects a bare / RESTRICT
    ``DROP SCHEMA`` on a non-empty dataset with BigQuery's
    ``resourceInUse`` error (DuckDB's own dependency error leaks the
    internal ``project__dataset`` schema name; CASCADE is unaffected),
    then resolves ``ddlOperationPerformed`` against pre-mutation target
    existence (CREATE / REPLACE / SKIP / DROP). Pinned by
    ``rest_crud/ddl_drop_schema_non_empty_restrict`` and the
    ``rest_crud/ddl_result_*`` corpus.
    """
    assert_drop_schema_allowed(bq_sql, project_id, ctx)
    return resolve_ddl_operation(bq_sql, statement_type, project_id, ctx)


def _parse_script_or_raise(bq_sql: str) -> Any:
    """Parse ``bq_sql`` to a :class:`Script`, routing lexer errors through the mapper.

    ADR 0022 §3: scripting-lexer errors (``Unterminated string literal``
    etc.) are raised directly as :class:`InvalidQueryError`; the mapper
    rewrites them into BigQuery-shape wording (``Syntax error: Unclosed
    string literal at [L:C]``) before the error reaches the route
    handler. Errors the mapper doesn't recognise propagate unchanged.
    """
    try:
        return parse_script(bq_sql)
    except DomainError as exc:
        translated = translate_runtime_error(exc)
        if translated is exc:
            raise
        raise translated from exc


async def _run_query_fast_paths(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    statement_type: str,
    now: Any,
    ctx: AppContext,
) -> JobMeta | None:
    """Intercept the versioning-DDL and row-access-policy DDL fast paths.

    Only applies to single-statement input — multi-statement scripts
    dispatch these per-statement inside :class:`ScriptInterpreter`
    (ADR 0023 §1.F) so the matching regexes don't greedy-match across
    statement boundaries. Returns ``None`` when ``bq_sql`` is neither a
    RAP DDL, a versioning DDL, nor a routine drop.
    """
    routine_drop = await _maybe_run_routine_drop(
        project_id=project_id,
        job_id=job_id,
        bq_sql=bq_sql,
        now=now,
        ctx=ctx,
    )
    if routine_drop is not None:
        return routine_drop
    rap_result = _maybe_run_row_access_ddl(project_id, bq_sql, ctx)
    if rap_result is not None:
        # ``classify_statement_type`` recognises both the CREATE and DROP
        # RAP forms via the same regexes the handler dispatched on, so
        # this is the correct type for either branch (a bare hardcoded
        # ``CREATE_ROW_ACCESS_POLICY`` would mislabel DROPs).
        return _build_ddl_job_meta(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            statement_type=statement_type,
            arrow_table=rap_result,
            now=now,
            ctx=ctx,
        )
    ddl_result = await _maybe_run_versioning_ddl(project_id, bq_sql, ctx)
    if ddl_result is not None:
        return _build_ddl_job_meta(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            statement_type=statement_type or "CREATE_SNAPSHOT_TABLE",
            arrow_table=ddl_result,
            now=now,
            ctx=ctx,
        )
    return None


def _build_ddl_job_meta(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    statement_type: str,
    arrow_table: pa.Table,
    now: Any,
    ctx: AppContext,
    ddl_operation: str | None = None,
) -> JobMeta:
    """Record the DDL-fast-path result and return the closing :class:`JobMeta`.

    The RAP-DDL, versioning-DDL, and routine-drop branches end with the
    same shape — register an empty result, bump the SQL-translation
    metric, and return a ``DONE`` job meta with a zero-row statistics
    block. ``ddl_operation`` overrides the static per-type mapping when
    the caller resolved it dynamically (e.g. a routine drop reporting
    ``SKIP`` for ``IF EXISTS`` over a missing target).
    """
    JOB_RESULTS[job_id] = arrow_table
    JOB_SCHEMAS[job_id] = build_response_schema(arrow_table.schema)
    ctx.metrics.sql_translation_total.labels(outcome="ok").inc()
    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="QUERY",
        state="DONE",
        configuration={"query": {"query": bq_sql}},
        statistics=_build_query_statistics(
            total_rows=0,
            statement_type=statement_type,
            num_dml_affected_rows=None,
            ddl_operation=ddl_operation,
        ),
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


async def _maybe_run_routine_drop(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    now: Any,
    ctx: AppContext,
) -> JobMeta | None:
    """Execute a single ``DROP {FUNCTION|PROCEDURE|TABLE FUNCTION}``, or return ``None``.

    Routine drops have no DuckDB counterpart — a procedure is not a
    DuckDB object and a UDF macro drop must mirror the registry
    bookkeeping — so the normal path would hand DuckDB SQL it rejects.
    This intercepts the drop, runs it against the catalog + UDF registry,
    and reports the BigQuery ``statementType`` (``DROP_FUNCTION`` /
    ``DROP_PROCEDURE`` / ``DROP_TABLE_FUNCTION``) with ``DROP`` / ``SKIP``.
    Returns ``None`` for every non-routine statement.
    """
    ref = detect_drop_routine(bq_sql, project_id)
    if ref is None:
        return None
    operation = await run_drop_routine(ref, ctx)
    return _build_ddl_job_meta(
        project_id=project_id,
        job_id=job_id,
        bq_sql=bq_sql,
        statement_type=ref.statement_type,
        arrow_table=_EMPTY_ARROW,
        now=now,
        ctx=ctx,
        ddl_operation=operation,
    )


async def _run_query_body(
    *,
    project_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    ctx: AppContext,
    is_scripted: bool,
    effective_caller: CallerIdentity,
) -> pa.Table:
    """Execute ``bq_sql`` via the scripting interpreter or the legacy single-SQL path.

    The legacy path also syncs ``CREATE [OR REPLACE] {SCHEMA|TABLE|VIEW}``
    outputs into the catalog so downstream lookups (versioning managers,
    INFORMATION_SCHEMA, the row-access rewriter's ``_expand_view``
    branch) find them. MATERIALIZED VIEW, CLONE, and SNAPSHOT forms
    route through dedicated managers and are not synced here.
    ``CREATE SCHEMA`` is synced first so a table created in a SQL-only
    dataset finds its dataset already registered (``sync_created_table``
    also auto-registers a missing dataset as a fallback). It then syncs
    ``DROP TABLE/VIEW/SCHEMA`` by removing the dropped object from the
    catalog (``sync_dropped_object``) so the relation or dataset
    disappears from those same surfaces, matching BigQuery.
    """
    if is_scripted:
        interpreter = ScriptInterpreter(ctx, project_id, caller=effective_caller)
        return await _run_script(interpreter, bq_sql, ctx)
    arrow_table = await _run_single_sql(
        project_id,
        bq_sql,
        query_params,
        ctx,
        caller=effective_caller,
    )
    sync_created_schema(bq_sql, project_id, ctx)
    sync_created_table(bq_sql, project_id, ctx)
    sync_created_view(bq_sql, project_id, ctx)
    sync_dropped_object(bq_sql, project_id, ctx)
    return arrow_table


def _finalize_statement_result(
    arrow_table: pa.Table,
    statement_type: str,
    *,
    bq_sql: str,
    project_id: str,
    ctx: AppContext,
) -> tuple[pa.Table, int | None, list[dict[str, Any]] | None]:
    """Shape the stored result to BigQuery's per-statement-type contract.

    DuckDB returns a 1-column status table (``Count`` for DML / most
    DDL, ``Success`` for drops) from non-SELECT statements; real
    BigQuery never exposes those. Recorded behaviour (the
    ``rest_crud/ddl_result_*`` corpus):

    * DML and ``TRUNCATE TABLE`` → empty result + the affected-row
      count surfaced as ``numDmlAffectedRows``.
    * ``CREATE TABLE`` / CTAS / ``CREATE VIEW`` → zero rows with the
      statement's analyzed schema (third tuple element; overrides the
      Arrow-derived response schema).
    * ``ALTER TABLE``, ``CREATE SCHEMA``, ``DROP TABLE/VIEW/SCHEMA`` →
      fully empty result.

    Anything else — SELECTs and multi-statement scripts (whose result
    the interpreter already shaped) — passes through unchanged.
    """
    if statement_type in _COUNT_TRIMMED_STATEMENTS:
        return _EMPTY_ARROW, _extract_dml_affected_rows(arrow_table), None
    if statement_type in _EMPTY_RESULT_DDL_STATEMENTS:
        return _EMPTY_ARROW, None, None
    if statement_type in _OBJECT_SCHEMA_DDL_STATEMENTS:
        return _EMPTY_ARROW, None, ddl_result_schema_fields(bq_sql, project_id, ctx)
    return arrow_table, None, None


#: Dataset id under which bqemulator advertises anonymous-result tables.
#: Real BigQuery uses ``_script<hash>`` for scripts and an internal
#: per-job anonymous table for interactive queries; the
#: ``api/routes/tables.py:get_table`` handler intercepts this dataset
#: and synthesises a response from ``JOB_RESULTS``.
_ANONYMOUS_RESULTS_DATASET = "_bqemu_anonymous"


def _build_query_configuration(
    bq_sql: str,
    project_id: str,
    job_id: str,
) -> dict[str, Any]:
    """Return ``configuration`` for a finished QUERY job.

    Always carries the original ``query`` text and a ``destinationTable``
    so REST clients that fetch the destination metadata after the job
    completes — notably ``dbt-bigquery``'s
    ``client.get_table(query_job.destination)`` post-execution step —
    see a non-``None`` ref and proceed instead of raising
    ``'NoneType' object has no attribute 'path'``.

    For ``CREATE [OR REPLACE] TABLE`` outputs the destination is the
    actual target table the executor materialised in DuckDB. For every
    other query (``SELECT``, ``INSERT``, ``UPDATE``, ``DELETE``,
    ``MERGE``, etc.) the destination is a synthetic anonymous-result
    ref under the reserved
    :data:`_ANONYMOUS_RESULTS_DATASET`; ``api/routes/tables.get_table``
    fans that out by reading the job's row count + schema from
    ``JOB_RESULTS``.
    """
    config: dict[str, Any] = {"query": {"query": bq_sql}}
    from bqemulator.catalog.ddl_sync import (
        _detect_plain_create_table,
        _split_target,
    )

    target = _detect_plain_create_table(bq_sql)
    if target is not None:
        p_id, d_id, t_id = _split_target(target, project_id)
        if d_id and t_id:
            config["query"]["destinationTable"] = {
                "projectId": p_id,
                "datasetId": d_id,
                "tableId": t_id,
            }
            return config

    # Fall through: synthesise an anonymous destination so the wire
    # shape always carries a non-``None`` ``destinationTable``.
    config["query"]["destinationTable"] = {
        "projectId": project_id,
        "datasetId": _ANONYMOUS_RESULTS_DATASET,
        "tableId": f"anon{job_id.replace('-', '')}",
    }
    return config


def _build_query_statistics(
    *,
    total_rows: int,
    statement_type: str,
    num_dml_affected_rows: int | None,
    ddl_operation: str | None = None,
    export_statistics: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Construct the canonical ``statistics`` dict for a query job.

    Always populates ``cacheHit=False`` (the emulator has no query
    cache, so real BigQuery's `False`-by-default is what every fresh
    query returns). ``statementType`` is omitted when the classifier
    returned ``""`` (unparseable). ``numDmlAffectedRows`` is included
    only for DML statements. ``ddlOperationPerformed`` carries the
    caller-resolved operation when ``ddl_operation`` is given (the
    main query path resolves CREATE / REPLACE / SKIP / DROP against
    pre-execution target existence); otherwise it falls back to the
    static per-statement-type mapping in
    :data:`bqemulator.jobs.ddl_result.DDL_OPERATION_BY_STATEMENT`.

    ``export_statistics`` carries ``(file_count, row_count)`` for an
    ``EXPORT_DATA`` job; when given, ``statistics.query`` gains the
    ``exportDataStatistics`` block plus the ``totalPartitionsProcessed``
    / ``transferredBytes`` fields BigQuery emits alongside it (the
    shape is pinned by ``http_corpus/jobs/export_csv_query_job``).

    The shape mirrors BigQuery's REST `Job.statistics.query` field;
    the conformance comparator at
    :mod:`tests.conformance._comparison._compare_job_metadata` reads
    the same keys.
    """
    query_stats: dict[str, Any] = {
        "totalBytesProcessed": "0",
        "totalRows": str(total_rows),
        "cacheHit": False,
    }
    if statement_type:
        query_stats["statementType"] = statement_type
        ddl_op = ddl_operation if ddl_operation is not None else ddl_operation_for(statement_type)
        if ddl_op:
            query_stats["ddlOperationPerformed"] = ddl_op
    if num_dml_affected_rows is not None:
        query_stats["numDmlAffectedRows"] = str(num_dml_affected_rows)
    if export_statistics is not None:
        file_count, exported_rows = export_statistics
        query_stats["totalPartitionsProcessed"] = "0"
        query_stats["transferredBytes"] = "0"
        query_stats["exportDataStatistics"] = {
            "fileCount": str(file_count),
            "rowCount": str(exported_rows),
        }
    return {"query": query_stats}


async def _run_single_sql(
    project_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    ctx: AppContext,
    *,
    caller: CallerIdentity,
) -> pa.Table:
    """Legacy fast path for a single SQL statement with BQ query params."""
    from bqemulator.sql.rewriter.information_schema import (
        expand_information_schema,
    )
    from bqemulator.sql.rewriter.unnest_offset import rewrite_unnest_offset
    from bqemulator.sql.rewriter.wildcard_expander import expand_wildcard_tables

    # Refresh any stale materialized views this query reads.
    await _refresh_dependent_mvs(project_id, bq_sql, ctx)
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
    translate_result = _translator.translate(
        bq_sql,
        schema=schema_dict or None,
        caller=caller,
    )
    match translate_result:
        case Err(error):
            raise error
        case Ok(duckdb_sql):
            pass
    try:
        duckdb_sql = rewrite_table_refs(duckdb_sql, project_id)
        duckdb_sql, param_values = bind_parameters(duckdb_sql, query_params)
        return ctx.engine.fetch_arrow(duckdb_sql, param_values or None)
    except DomainError as exc:
        # ADR 0022 §3: pre-execution domain errors (RAP denials,
        # malformed-id ValidationErrors from the SQL pipeline) are
        # re-shaped to BigQuery wire-format ``reason`` / ``location`` /
        # ``message_pattern`` conventions before reaching the route
        # handler. The mapper either re-translates (e.g.
        # ValidationError "Invalid X id for SQL" → InvalidQueryError
        # "Function not found …") or returns the original error
        # unchanged.
        translated = translate_runtime_error(exc, duckdb_sql=duckdb_sql)
        if translated is exc:
            raise
        raise translated from exc
    except Exception as exc:
        raise translate_runtime_error(exc, duckdb_sql=duckdb_sql) from exc


async def _run_script(
    interpreter: ScriptInterpreter,
    source: str,
    ctx: AppContext,  # noqa: ARG001 — kept for future cache / metrics hooks
) -> pa.Table:
    """Execute a script and return its final result table (or empty)."""
    result = await interpreter.run(source)
    if result.final_table is not None:
        return result.final_table
    return pa.table({})


async def execute_load_job(
    project_id: str,
    job_id: str,
    config: dict[str, Any],
    ctx: AppContext,
) -> JobMeta:
    """Execute a load job — import data from files into a table.

    Supports CSV, NEWLINE_DELIMITED_JSON, PARQUET, AVRO via DuckDB's
    native readers, plus ORC via the optional ``pyorc`` package (G1).
    """
    now = ctx.clock.now()
    job = _parse_load_job_config(project_id, config)

    # CREATE_IF_NEEDED — materialise the destination from the explicit
    # schema (bq CLI / SDK clients pass ``load.schema.fields``) before
    # touching DuckDB. CREATE_NEVER + missing table → notFound. If a
    # schema is not supplied (autodetect path), the existing DuckDB
    # COPY/INSERT call below will raise a binder error which the load
    # error wrapper translates to a proper ``invalid`` job error.
    if (
        job.create_disposition == "CREATE_IF_NEEDED"
        and ctx.catalog.get_table(job.project_id, job.dataset_id, job.table_id) is None
    ):
        _maybe_create_load_destination(
            dest_project=job.project_id,
            dest_dataset=job.dataset_id,
            dest_table_id=job.table_id,
            load_config=job.raw_config,
            now=now,
            ctx=ctx,
        )

    # Resolve URIs: gs:// → local path under GCS_LOCAL_ROOT, or file:// → local.
    resolved_paths = [_resolve_uri(uri, ctx) for uri in job.source_uris]
    for path in resolved_paths:
        _validate_local_path(path)

    async with ctx.engine.write_lock():
        _apply_load_write_disposition(job, ctx)
        for path in resolved_paths:
            _load_path_into_target(path, job, ctx)

    new_count = _refresh_load_row_count(job, ctx)

    table_meta = ctx.catalog.get_table(job.project_id, job.dataset_id, job.table_id)
    if table_meta is not None:
        ctx.catalog.update_table(table_meta.model_copy(update={"num_rows": new_count}))
        # Capture a snapshot + notify dependents. The load path
        # already released its write lock; reacquire for the snapshot CTAS.
        async with ctx.engine.write_lock():
            ctx.snapshots.record_change(job.project_id, job.dataset_id, job.table_id)

    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="LOAD",
        state="DONE",
        configuration=config,
        statistics={"load": {"outputRows": str(new_count)}},
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


@dataclass(frozen=True, slots=True)
class _LoadJobConfig:
    """Parsed shape of a ``configuration.load`` block.

    Carrying the resolved values as a frozen dataclass keeps the
    per-step helpers (write-disposition, per-format loader,
    row-count refresh) trivially callable without re-deriving the
    config from the raw dict on each call.
    """

    project_id: str
    dataset_id: str
    table_id: str
    target_ref: str
    source_uris: list[str]
    source_format: str
    write_disposition: str
    create_disposition: str
    raw_config: dict[str, Any]


def _parse_load_job_config(project_id: str, config: dict[str, Any]) -> _LoadJobConfig:
    """Extract a typed :class:`_LoadJobConfig` from the raw REST payload."""
    load_config = config.get("load", {})
    dest_table = load_config.get("destinationTable", {})
    dest_project = dest_table.get("projectId", project_id)
    dest_dataset = dest_table.get("datasetId", "")
    dest_table_id = dest_table.get("tableId", "")
    return _LoadJobConfig(
        project_id=dest_project,
        dataset_id=dest_dataset,
        table_id=dest_table_id,
        target_ref=quoted_table_ref(dest_project, dest_dataset, dest_table_id),
        source_uris=load_config.get("sourceUris", []),
        source_format=load_config.get("sourceFormat", "CSV").upper(),
        write_disposition=load_config.get("writeDisposition", "WRITE_APPEND"),
        create_disposition=load_config.get("createDisposition", "CREATE_IF_NEEDED"),
        raw_config=load_config,
    )


def _apply_load_write_disposition(job: _LoadJobConfig, ctx: AppContext) -> None:
    """Honour ``writeDisposition`` (WRITE_TRUNCATE / WRITE_EMPTY) before the load runs."""
    if job.write_disposition == "WRITE_TRUNCATE":
        ctx.engine.execute(f"DELETE FROM {job.target_ref}")
        return
    if job.write_disposition == "WRITE_EMPTY":
        count = ctx.engine.execute(
            f"SELECT COUNT(*) FROM {job.target_ref}",
        ).fetchone()
        if count and count[0] > 0:
            raise InvalidQueryError(
                f"Table {job.dataset_id}.{job.table_id} is not empty and "
                "writeDisposition is WRITE_EMPTY",
            )


def _load_path_into_target(path: str, job: _LoadJobConfig, ctx: AppContext) -> None:
    """Dispatch ``path`` to the per-format loader for :attr:`_LoadJobConfig.source_format`.

    DuckDB accepts ``?`` placeholders for file paths in ``COPY`` and
    ``read_*`` functions, so the per-format helpers parameterise rather
    than string-concatenate to shut the door on path-injection (even
    though :func:`_validate_local_path` already vetted the value).
    """
    handler = _LOAD_FORMAT_HANDLERS.get(job.source_format)
    if handler is None:
        raise InvalidQueryError(f"Unknown source format: {job.source_format}")
    handler(path, job.target_ref, ctx)


def _load_csv(path: str, target_ref: str, ctx: AppContext) -> None:
    ctx.engine.execute(
        f"COPY {target_ref} FROM ? (FORMAT CSV, HEADER)",
        [path],
    )


def _load_json(path: str, target_ref: str, ctx: AppContext) -> None:
    ctx.engine.execute(
        f"COPY {target_ref} FROM ? (FORMAT JSON)",
        [path],
    )


def _load_parquet(path: str, target_ref: str, ctx: AppContext) -> None:
    ctx.engine.execute(
        f"INSERT INTO {target_ref} SELECT * FROM read_parquet(?)",
        [path],
    )


def _load_avro(path: str, target_ref: str, ctx: AppContext) -> None:
    """Route Avro inputs through the fastavro fallback when the writer schema is decimal-logical.

    DuckDB's ``avro`` extension provides ``read_avro`` and is loaded at
    engine boot (best-effort) via
    :meth:`DuckDBEngine._load_format_extensions`. If loading failed the
    SELECT below raises a ``Table Function with name read_avro does not
    exist`` catalog error, which we surface as
    :class:`UnsupportedFeatureError`.

    G1-follow-up (2026-05-20): when the Avro file uses the ``decimal``
    logical type, DuckDB returns the column as BLOB and the auto-cast
    to NUMERIC fails — pre-detect this via the writer schema and route
    through the fastavro fallback (which decodes ``decimal`` to Python
    ``Decimal`` directly). All other Avro shapes stay on the fast
    DuckDB path. Any other failure (missing file, genuine schema
    mismatch) bubbles through error_mapper unchanged.
    """
    if is_decimal_logical_avro(path):
        _insert_via_arrow_view(
            arrow_table=read_avro_to_arrow(path),
            view_name="_bqemu_avro_load",
            target_ref=target_ref,
            ctx=ctx,
        )
        return
    try:
        ctx.engine.execute(
            f"INSERT INTO {target_ref} SELECT * FROM read_avro(?)",
            [path],
        )
    except Exception as exc:
        if _is_missing_extension_error(exc, "read_avro"):
            raise UnsupportedFeatureError(
                "Load from AVRO requires DuckDB's ``avro`` "
                "extension. Re-enable BQEMU_ENABLE_FORMAT_"
                "EXTENSIONS or run the emulator with "
                "network access to extensions.duckdb.org.",
            ) from exc
        raise


def _load_orc(path: str, target_ref: str, ctx: AppContext) -> None:
    """Route ORC inputs through the pyorc → Arrow bridge.

    DuckDB has no native ORC reader, so the pyorc fallback decodes the
    file into a pyarrow Table and we register it as a DuckDB view via
    ``connection.register`` so the INSERT picks up the schema
    correctly.
    """
    _insert_via_arrow_view(
        arrow_table=read_orc_to_arrow(path),
        view_name="_bqemu_orc_load",
        target_ref=target_ref,
        ctx=ctx,
    )


def _insert_via_arrow_view(
    *,
    arrow_table: pa.Table,
    view_name: str,
    target_ref: str,
    ctx: AppContext,
) -> None:
    """Register an Arrow table as a temp DuckDB view, INSERT-SELECT, then unregister.

    Shared between :func:`_load_avro` (fastavro-decoded decimal-logical
    files) and :func:`_load_orc` (pyorc-decoded files). The
    ``unregister`` runs in a ``finally`` so the temp view doesn't leak
    on insert failure.
    """
    ctx.engine.connection.register(view_name, arrow_table)
    try:
        ctx.engine.execute(
            f"INSERT INTO {target_ref} SELECT * FROM {view_name}",
        )
    finally:
        ctx.engine.connection.unregister(view_name)


#: Per-source-format load handlers. The dispatch dict keys are the
#: canonical BigQuery ``sourceFormat`` values (also accepting the
#: ``"JSON"`` alias for ``NEWLINE_DELIMITED_JSON`` that some clients
#: emit). Unknown formats fall through to an :class:`InvalidQueryError`
#: in :func:`_load_path_into_target`.
_LOAD_FORMAT_HANDLERS: dict[str, Callable[[str, str, AppContext], None]] = {
    "CSV": _load_csv,
    "NEWLINE_DELIMITED_JSON": _load_json,
    "JSON": _load_json,
    "PARQUET": _load_parquet,
    "AVRO": _load_avro,
    "ORC": _load_orc,
}


def _refresh_load_row_count(job: _LoadJobConfig, ctx: AppContext) -> int:
    """Return the destination row count after the load completes."""
    count_result = ctx.engine.execute(
        f"SELECT COUNT(*) FROM {job.target_ref}",
    ).fetchone()
    return count_result[0] if count_result else 0


async def execute_extract_job(
    project_id: str,
    job_id: str,
    config: dict[str, Any],
    ctx: AppContext,
) -> JobMeta:
    """Execute an extract job — export table data to files."""
    now = ctx.clock.now()
    extract_config = config.get("extract", {})

    source_table = extract_config.get("sourceTable", {})
    src_project = source_table.get("projectId", project_id)
    src_dataset = source_table.get("datasetId", "")
    src_table_id = source_table.get("tableId", "")
    dest_uris = extract_config.get("destinationUris", [])
    dest_format = extract_config.get("destinationFormat", "CSV").upper()

    src_ref = quoted_table_ref(src_project, src_dataset, src_table_id)
    dest_path = (
        _resolve_uri(dest_uris[0], ctx) if dest_uris else "/tmp/extract_output"  # noqa: S108
    )
    _validate_local_path(dest_path)
    select_sql = f"SELECT * FROM {src_ref}"

    if dest_format not in _EXPORT_FORMATS:
        raise InvalidQueryError(f"Unknown destination format: {dest_format}")
    # Shared COPY writer (also used by the EXPORT DATA statement). Extract
    # always emits a CSV header and applies no compression or custom
    # delimiter, preserving the prior behaviour.
    _copy_relation_to_file(select_sql, dest_path, dest_format, ctx)

    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="EXTRACT",
        state="DONE",
        configuration=config,
        statistics={},
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


async def execute_copy_job(
    project_id: str,
    job_id: str,
    config: dict[str, Any],
    ctx: AppContext,
) -> JobMeta:
    """Execute a copy job — copy data between tables.

    Supports BigQuery's full ``JobConfigurationTableCopy`` shape: either
    ``sourceTable`` (singular, used by the official SDK clients) or
    ``sourceTables`` (plural array, used by the ``bq`` CLI for the
    multi-source concatenation case). Honours ``operationType`` —
    ``COPY`` (default), ``SNAPSHOT``, ``CLONE``, ``RESTORE`` — by
    dispatching SNAPSHOT/CLONE to the versioning managers so the
    wire-level ``bq cp --snapshot`` / ``bq cp --clone`` paths produce
    catalog entries equivalent to the DDL-driven SDK path.
    """
    now = ctx.clock.now()
    copy_config = config.get("copy", {})

    source_table = copy_config.get("sourceTable")
    if not source_table:
        source_tables = copy_config.get("sourceTables") or []
        source_table = source_tables[0] if source_tables else {}
    dest_table = copy_config.get("destinationTable", {})
    write_disposition = copy_config.get("writeDisposition", "WRITE_APPEND")
    operation_type = copy_config.get("operationType", "COPY") or "COPY"

    src_proj = source_table.get("projectId", project_id)
    src_ds = source_table.get("datasetId", "")
    src_table_id = source_table.get("tableId", "")
    dst_proj = dest_table.get("projectId", project_id)
    dst_ds = dest_table.get("datasetId", "")
    dst_table_id = dest_table.get("tableId", "")

    if operation_type == "SNAPSHOT":
        from bqemulator.versioning.snapshot_table import SnapshotTableManager

        await SnapshotTableManager(ctx).create(
            dst_proj,
            dst_ds,
            dst_table_id,
            src_proj,
            src_ds,
            src_table_id,
        )
    elif operation_type == "CLONE":
        from bqemulator.versioning.clone import CloneManager

        await CloneManager(ctx).create(
            dst_proj,
            dst_ds,
            dst_table_id,
            src_proj,
            src_ds,
            src_table_id,
        )
    elif operation_type == "RESTORE":
        # RESTORE inverts SNAPSHOT: materialise the destination as a
        # regular table whose rows match the snapshot source.
        if not src_table_id or not dst_table_id:
            raise InvalidQueryError(
                "RESTORE requires both sourceTable and destinationTable",
            )
        await _copy_table_into_destination(
            src_proj=src_proj,
            src_ds=src_ds,
            src_table_id=src_table_id,
            dst_proj=dst_proj,
            dst_ds=dst_ds,
            dst_table_id=dst_table_id,
            write_disposition="WRITE_TRUNCATE",
            create_if_needed=True,
            ctx=ctx,
        )
    else:
        create_disposition = copy_config.get(
            "createDisposition",
            "CREATE_IF_NEEDED",
        )
        await _copy_table_into_destination(
            src_proj=src_proj,
            src_ds=src_ds,
            src_table_id=src_table_id,
            dst_proj=dst_proj,
            dst_ds=dst_ds,
            dst_table_id=dst_table_id,
            write_disposition=write_disposition,
            create_if_needed=(create_disposition == "CREATE_IF_NEEDED"),
            ctx=ctx,
        )

    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="COPY",
        state="DONE",
        configuration=config,
        statistics={},
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


def _maybe_create_load_destination(
    *,
    dest_project: str,
    dest_dataset: str,
    dest_table_id: str,
    load_config: dict[str, Any],
    now: Any,
    ctx: AppContext,
) -> None:
    """Materialise the destination table for a load job with explicit schema.

    Mirrors ``tables.insert``: builds the DuckDB CREATE TABLE DDL from
    the ``load.schema.fields`` payload and registers a ``TableMeta``.
    No-op when the request omits a schema (autodetect path); the
    caller's downstream COPY/INSERT will raise a binder error which
    the load error wrapper converts to a proper async job error.
    """
    from bqemulator.api.routes.tables import _field_to_rest, _parse_schema_fields
    from bqemulator.catalog.models import TableMeta, TableSchema
    from bqemulator.storage.type_map import bq_schema_to_duckdb_columns

    schema_raw = load_config.get("schema") or {}
    fields_raw = schema_raw.get("fields") or []
    if not fields_raw:
        return
    if ctx.catalog.get_dataset(dest_project, dest_dataset) is None:
        raise resource_not_found(
            ResourceRef("dataset", dest_project, dest_dataset),
        )

    field_models = _parse_schema_fields(fields_raw)
    schema = TableSchema(fields=field_models)
    duckdb_cols = bq_schema_to_duckdb_columns(
        [_field_to_rest(f) for f in field_models],
    )
    col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in duckdb_cols)
    target_ref = quoted_table_ref(dest_project, dest_dataset, dest_table_id)

    ctx.engine.execute(f"CREATE TABLE {target_ref} ({col_defs})")
    meta = TableMeta(
        project_id=dest_project,
        dataset_id=dest_dataset,
        table_id=dest_table_id,
        table_type="TABLE",
        schema=schema,
        labels={},
        creation_time=now,
        last_modified_time=now,
        num_rows=0,
        num_bytes=0,
        etag=generate_etag(dest_project, dest_dataset, dest_table_id, "TABLE", str(now)),
    )
    ctx.catalog.create_table(meta)


async def _copy_table_into_destination(
    *,
    src_proj: str,
    src_ds: str,
    src_table_id: str,
    dst_proj: str,
    dst_ds: str,
    dst_table_id: str,
    write_disposition: str,
    create_if_needed: bool,
    ctx: AppContext,
) -> None:
    """COPY a source table's rows into a destination, materialising it if needed.

    Mirrors BigQuery's ``copy`` job + ``createDisposition=CREATE_IF_NEEDED``
    semantics: if the destination doesn't exist and creation is allowed,
    a regular ``TABLE`` is materialised with the source's schema, partition,
    and clustering. Honours ``writeDisposition`` (WRITE_APPEND /
    WRITE_TRUNCATE / WRITE_EMPTY) for the rows-only path when the
    destination already exists.
    """
    src_meta = ctx.catalog.get_table(src_proj, src_ds, src_table_id)
    if src_meta is None:
        raise resource_not_found(
            ResourceRef("table", src_proj, src_ds, src_table_id),
        )
    if ctx.catalog.get_dataset(dst_proj, dst_ds) is None:
        raise resource_not_found(ResourceRef("dataset", dst_proj, dst_ds))

    src_ref = quoted_table_ref(src_proj, src_ds, src_table_id)
    dst_ref = quoted_table_ref(dst_proj, dst_ds, dst_table_id)
    dst_meta = ctx.catalog.get_table(dst_proj, dst_ds, dst_table_id)

    async with ctx.engine.write_lock():
        if dst_meta is None:
            if not create_if_needed:
                raise resource_not_found(
                    ResourceRef("table", dst_proj, dst_ds, dst_table_id),
                )
            _create_copy_destination(
                src_meta=src_meta,
                src_ref=src_ref,
                dst_proj=dst_proj,
                dst_ds=dst_ds,
                dst_table_id=dst_table_id,
                dst_ref=dst_ref,
                ctx=ctx,
            )
        else:
            _apply_copy_write_disposition(
                dst_ds=dst_ds,
                dst_table_id=dst_table_id,
                dst_ref=dst_ref,
                write_disposition=write_disposition,
                ctx=ctx,
            )
            ctx.engine.execute(f"INSERT INTO {dst_ref} SELECT * FROM {src_ref}")

        ctx.snapshots.record_change(dst_proj, dst_ds, dst_table_id)


def _create_copy_destination(
    *,
    src_meta: Any,
    src_ref: str,
    dst_proj: str,
    dst_ds: str,
    dst_table_id: str,
    dst_ref: str,
    ctx: AppContext,
) -> None:
    """Materialise the destination of a copy job under CREATE_IF_NEEDED.

    Issues ``CREATE TABLE ... AS SELECT * FROM ...`` against DuckDB then
    registers a :class:`TableMeta` carrying the source's schema,
    partitioning, and clustering so downstream lookups against the
    new destination find the same shape as the source.
    """
    from bqemulator.catalog.models import TableMeta, TableSchema

    ctx.engine.execute(
        f"CREATE TABLE {dst_ref} AS SELECT * FROM {src_ref}",
    )
    now = ctx.clock.now()
    count_row = ctx.engine.execute(
        f"SELECT COUNT(*) FROM {dst_ref}",
    ).fetchone()
    num_rows = int(count_row[0]) if count_row else 0
    new_meta = TableMeta(
        project_id=dst_proj,
        dataset_id=dst_ds,
        table_id=dst_table_id,
        table_type="TABLE",
        schema=(src_meta.schema_ or TableSchema()),
        labels={},
        time_partitioning=src_meta.time_partitioning,
        range_partitioning=src_meta.range_partitioning,
        clustering=src_meta.clustering,
        creation_time=now,
        last_modified_time=now,
        num_rows=num_rows,
        num_bytes=0,
        etag=generate_etag(
            dst_proj,
            dst_ds,
            dst_table_id,
            "TABLE",
            str(now),
        ),
    )
    ctx.catalog.create_table(new_meta)


def _apply_copy_write_disposition(
    *,
    dst_ds: str,
    dst_table_id: str,
    dst_ref: str,
    write_disposition: str,
    ctx: AppContext,
) -> None:
    """Honour ``writeDisposition`` for the rows-only path of a copy job.

    ``WRITE_TRUNCATE`` empties the destination before the INSERT;
    ``WRITE_EMPTY`` raises when the destination already carries rows
    (BigQuery's documented refusal semantic). ``WRITE_APPEND`` is the
    no-op default and falls through.
    """
    if write_disposition == "WRITE_TRUNCATE":
        ctx.engine.execute(f"DELETE FROM {dst_ref}")
        return
    if write_disposition == "WRITE_EMPTY":
        existing = ctx.engine.execute(
            f"SELECT COUNT(*) FROM {dst_ref}",
        ).fetchone()
        if existing and existing[0] > 0:
            raise InvalidQueryError(
                f"Table {dst_ds}.{dst_table_id} is not empty and writeDisposition is WRITE_EMPTY",
            )


def _is_missing_extension_error(exc: BaseException, function_name: str) -> bool:
    """Return True if *exc* signals an unloaded DuckDB extension (G1).

    DuckDB raises a Catalog Error of the shape ``Table Function with
    name "X" is not in the catalog, but it exists in the <ext>
    extension`` (or ``Copy Function with name "X" is not in the
    catalog…`` for ``COPY``). We match on the disambiguating tail so
    we don't mis-classify legitimate runtime failures (missing files,
    schema mismatches) as "extension unavailable".
    """
    msg = str(exc)
    return (
        (f'with name "{function_name}"' in msg or f"with name '{function_name}'" in msg)
        and "extension" in msg
        and "not in the catalog" in msg
    )


def _resolve_uri(uri: str, ctx: AppContext) -> str:
    """Resolve a GCS or file URI to a local filesystem path.

    - ``gs://bucket/path`` → ``{gcs_local_root}/bucket/path``
    - ``file:///path`` → ``/path``
    - bare path → returned as-is
    """
    if uri.startswith("gs://"):
        gcs_root = ctx.settings.gcs_local_root
        if gcs_root is None:
            raise InvalidQueryError(
                "Cannot resolve gs:// URIs without BQEMU_GCS_LOCAL_ROOT configured",
            )
        # Strip gs:// prefix.
        relative = uri[5:]
        return str(gcs_root / relative)
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def _validate_local_path(path: str) -> None:
    """Reject path strings containing SQL-dangerous or shell-dangerous bytes.

    Used at the COPY FROM/TO boundary so a malicious ``sourceUris`` or
    ``destinationUris`` value can't close the string literal and smuggle
    in arbitrary DuckDB SQL — even though the ids surrounding it are
    already validated.
    """
    if not path:
        raise InvalidQueryError("Empty file path")
    forbidden = ("'", '"', ";", "\n", "\r", "\0", "`")
    for bad in forbidden:
        if bad in path:
            raise InvalidQueryError(f"Invalid character {bad!r} in file path")


# ---------------------------------------------------------------------------
# EXPORT DATA statement — RFC 0001 / ADR 0043
# ---------------------------------------------------------------------------

#: Canonical destination formats shared by the extract job and the
#: EXPORT DATA statement. ``JSON`` is accepted as an alias for
#: ``NEWLINE_DELIMITED_JSON``. ``ORC`` is intentionally absent — BigQuery
#: does not export ORC (parity; see ``out-of-scope.md``).
_EXPORT_FORMATS: frozenset[str] = frozenset(
    {"CSV", "NEWLINE_DELIMITED_JSON", "JSON", "PARQUET", "AVRO"},
)

#: Valid ``compression`` values per canonical format (upper-cased).
_COMPRESSION_BY_FORMAT: dict[str, frozenset[str]] = {
    "CSV": frozenset({"GZIP", "NONE"}),
    "NEWLINE_DELIMITED_JSON": frozenset({"GZIP", "NONE"}),
    "PARQUET": frozenset({"SNAPPY", "GZIP", "ZSTD", "NONE"}),
    "AVRO": frozenset({"DEFLATE", "SNAPPY", "NONE"}),
}

#: OPTIONS keys the EXPORT DATA statement accepts (besides ``format``,
#: which SQLGlot surfaces as a dedicated ``FileFormatProperty``).
_KNOWN_EXPORT_OPTIONS: frozenset[str] = frozenset(
    {
        "uri",
        "format",
        "compression",
        "overwrite",
        "header",
        "field_delimiter",
        "use_avro_logical_types",
    },
)

#: CSV-only OPTIONS — rejected on any other format for parity with
#: BigQuery's option/format validation.
_CSV_ONLY_OPTIONS: frozenset[str] = frozenset({"header", "field_delimiter"})

_EXPORT_DATA_RE = re.compile(r"^\s*EXPORT\s+DATA\b", re.IGNORECASE)

_AVRO_EXPORT_EXTENSION_MSG = (
    "Export to AVRO requires DuckDB's ``avro`` extension. Re-enable "
    "BQEMU_ENABLE_FORMAT_EXTENSIONS or run the emulator with network "
    "access to extensions.duckdb.org."
)


@dataclass(frozen=True)
class _ExportOptions:
    """Validated, normalised ``EXPORT DATA`` OPTIONS for one statement."""

    uri: str
    format: str  # canonical: CSV | NEWLINE_DELIMITED_JSON | PARQUET | AVRO
    compression: str | None  # upper-cased, or None
    overwrite: bool
    header: bool  # CSV only
    field_delimiter: str | None  # CSV only, resolved single char


@dataclass(frozen=True)
class _ExportRequest:
    """A parsed ``EXPORT DATA`` statement: its OPTIONS plus the inner query."""

    options: _ExportOptions
    select_sql: str


@dataclass(frozen=True)
class _ExportOutcome:
    """The result of writing an export: row count and the files written."""

    rows: int
    file_count: int
    uris: list[str]


def _build_copy_clause(
    fmt: str,
    *,
    header: bool,
    field_delimiter: str | None,
    compression: str | None,
) -> str:
    """Render the DuckDB ``COPY ... (<clause>)`` option list for ``fmt``.

    ``fmt`` is a canonical export format; ``NEWLINE_DELIMITED_JSON`` and
    ``JSON`` both map to DuckDB's ``FORMAT JSON``. CSV honours ``header``
    and ``field_delimiter``; CSV / JSON / PARQUET honour ``compression``.
    AVRO compression is not forwarded — DuckDB's ``avro`` COPY writer does
    not expose a codec option (see ADR 0043 unresolved questions).
    """
    fmt = fmt.upper()
    comp = compression.lower() if compression and compression.upper() != "NONE" else None
    if fmt == "CSV":
        parts = ["FORMAT CSV", "HEADER" if header else "HEADER false"]
        if field_delimiter is not None:
            parts.append(f"DELIMITER '{field_delimiter}'")
        if comp:
            parts.append(f"COMPRESSION {comp}")
        return ", ".join(parts)
    if fmt in ("NEWLINE_DELIMITED_JSON", "JSON"):
        parts = ["FORMAT JSON"]
        if comp:
            parts.append(f"COMPRESSION {comp}")
        return ", ".join(parts)
    if fmt == "PARQUET":
        parts = ["FORMAT PARQUET"]
        if comp:
            parts.append(f"COMPRESSION {comp}")
        return ", ".join(parts)
    if fmt == "AVRO":
        return "FORMAT AVRO"
    raise InvalidQueryError(f"Unknown destination format: {fmt}")


def _copy_relation_to_file(
    relation_sql: str,
    dest_path: str,
    fmt: str,
    ctx: AppContext,
    *,
    header: bool = True,
    field_delimiter: str | None = None,
    compression: str | None = None,
) -> None:
    """Write a DuckDB relation to ``dest_path`` via ``COPY ... TO``.

    Shared by the extract job (``relation_sql`` = ``SELECT * FROM <table>``)
    and the EXPORT DATA statement (``relation_sql`` = ``SELECT * FROM
    <registered Arrow view>``). The path is already whitelisted by
    :func:`_validate_local_path`, so it is safe to embed as a literal. An
    AVRO write whose extension is unavailable is surfaced as
    :class:`UnsupportedFeatureError`; other failures bubble unchanged.
    """
    clause = _build_copy_clause(
        fmt,
        header=header,
        field_delimiter=field_delimiter,
        compression=compression,
    )
    copy_sql = f"COPY ({relation_sql}) TO '{dest_path}' ({clause})"
    if fmt.upper() == "AVRO":
        try:
            ctx.engine.execute(copy_sql)
        except Exception as exc:
            if _is_missing_extension_error(exc, "avro"):
                raise UnsupportedFeatureError(_AVRO_EXPORT_EXTENSION_MSG) from exc
            raise
    else:
        ctx.engine.execute(copy_sql)


def _ensure_parent_dir(path: str) -> None:
    """Create the parent directory of ``path`` if it does not yet exist."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _shard_offsets(num_rows: int, shard_count: int) -> list[tuple[int, int]]:
    """Split ``num_rows`` into ``shard_count`` contiguous ``(offset, length)`` ranges.

    Rows are distributed as evenly as possible, earlier shards taking the
    remainder. ``num_rows == 0`` yields a single empty range so a wildcard
    export of an empty result still writes one (header-only) file, matching
    BigQuery.
    """
    base, remainder = divmod(num_rows, shard_count)
    offsets: list[tuple[int, int]] = []
    start = 0
    for i in range(shard_count):
        length = base + (1 if i < remainder else 0)
        offsets.append((start, length))
        start += length
    return offsets


def _opt_literal_str(node: Any) -> str:
    """Return the string payload of a SQLGlot option value node."""
    from sqlglot import exp

    if isinstance(node, exp.Literal):
        return str(node.this)
    return str(node)


def _opt_literal_bool(node: Any) -> bool:
    """Return the boolean payload of a SQLGlot option value node."""
    from sqlglot import exp

    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Literal):
        return str(node.this).strip().lower() == "true"
    return bool(node)


def _resolve_field_delimiter(raw: str) -> str:
    r"""Normalise and validate a CSV ``field_delimiter`` to a single safe char.

    ``tab`` / ``\t`` resolve to a literal tab (matching BigQuery). The
    result must be exactly one character and must not contain bytes that
    could break out of the DuckDB ``DELIMITER '...'`` literal.
    """
    if raw in ("\\t", "tab", "TAB", "\t"):
        return "\t"
    if len(raw) != 1:
        raise InvalidQueryError(
            "EXPORT DATA field_delimiter must be a single character (or 'tab').",
        )
    if raw in ("'", '"', "\\", "\n", "\r", "\0", "`"):
        raise InvalidQueryError(f"Invalid character {raw!r} in field_delimiter")
    return raw


def _normalize_export_format(raw: str) -> str:
    """Upper-case a ``format`` value; reject ORC / unknown values like BigQuery.

    BigQuery does not export ORC, but it rejects ``format='ORC'`` exactly the
    way it rejects any unrecognised value — as an invalid ``format`` OPTIONS
    value (``invalidQuery`` / HTTP 400), not as a distinct "unsupported
    feature". The recorded conformance baseline
    (``sql_corpus/export_data/export_orc_rejected``) pins the exact message,
    so ORC carries no special case here.
    """
    fmt = raw.strip().upper()
    if fmt not in _EXPORT_FORMATS:
        raise InvalidQueryError(
            f"'{raw}' is not a valid value; failed to set 'format' in EXPORT DATA OPTIONS",
            location="query",
        )
    return "NEWLINE_DELIMITED_JSON" if fmt == "JSON" else fmt


def _extract_export_options(properties: Any) -> _ExportOptions:
    """Parse and validate the ``OPTIONS(...)`` of an ``EXPORT DATA`` statement.

    ``properties`` is the SQLGlot ``Properties`` node. ``format`` arrives as
    a dedicated ``FileFormatProperty``; every other option is a generic
    ``Property`` keyed by name. Unknown options, format/option mismatches,
    and invalid compression values are rejected with a clear error.
    """
    from sqlglot import exp

    if properties is None:
        raise InvalidQueryError("EXPORT DATA requires an OPTIONS(...) list.")
    fmt_raw: str | None = None
    values: dict[str, Any] = {}
    for prop in properties.expressions:
        if isinstance(prop, exp.FileFormatProperty):
            fmt_raw = _opt_literal_str(prop.this)
            continue
        key = (prop.this.name if hasattr(prop.this, "name") else str(prop.this)).lower()
        if key == "format":
            fmt_raw = _opt_literal_str(prop.args.get("value"))
            continue
        if key not in _KNOWN_EXPORT_OPTIONS:
            raise InvalidQueryError(f"Unknown EXPORT DATA option: {key}")
        values[key] = prop.args.get("value")

    fmt = _normalize_export_format(fmt_raw) if fmt_raw is not None else "CSV"

    if "uri" not in values:
        raise InvalidQueryError("Option 'uri' is missing or empty.")
    uri = _opt_literal_str(values["uri"])
    if not uri:
        raise InvalidQueryError("Option 'uri' is missing or empty.")

    for csv_only in _CSV_ONLY_OPTIONS:
        if csv_only in values and fmt != "CSV":
            raise InvalidQueryError(
                f"EXPORT DATA option '{csv_only}' is only valid for FORMAT CSV.",
            )
    if "use_avro_logical_types" in values and fmt != "AVRO":
        raise InvalidQueryError(
            "EXPORT DATA option 'use_avro_logical_types' is only valid for FORMAT AVRO.",
        )

    compression: str | None = None
    if "compression" in values:
        compression = _opt_literal_str(values["compression"]).upper()
        allowed = _COMPRESSION_BY_FORMAT.get(fmt, frozenset())
        if compression not in allowed:
            raise InvalidQueryError(
                f"EXPORT DATA compression '{compression}' is not valid for "
                f"FORMAT {fmt}; allowed: {', '.join(sorted(allowed))}.",
            )

    header = _opt_literal_bool(values["header"]) if "header" in values else True
    field_delimiter = (
        _resolve_field_delimiter(_opt_literal_str(values["field_delimiter"]))
        if "field_delimiter" in values
        else None
    )
    overwrite = _opt_literal_bool(values["overwrite"]) if "overwrite" in values else False

    return _ExportOptions(
        uri=uri,
        format=fmt,
        compression=compression,
        overwrite=overwrite,
        header=header,
        field_delimiter=field_delimiter,
    )


def parse_export_data(bq_sql: str) -> _ExportRequest | None:
    """Parse ``bq_sql`` as an ``EXPORT DATA`` statement, or return ``None``.

    Returns ``None`` for any statement that is not ``EXPORT DATA`` (a cheap
    regex gate runs before the full SQLGlot parse). Raises
    :class:`UnsupportedFeatureError` for ``EXPORT DATA WITH CONNECTION``
    (external sinks are out of scope) and :class:`InvalidQueryError` for a
    malformed statement or invalid OPTIONS.
    """
    if not _EXPORT_DATA_RE.match(bq_sql):
        return None
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — non-EXPORT or unparseable: defer to the normal path
        return None
    if not isinstance(tree, exp.Export):  # pragma: no cover — regex gate already matched
        return None
    if tree.args.get("connection"):
        raise UnsupportedFeatureError(
            "EXPORT DATA WITH CONNECTION is not supported; bqemulator exports "
            "to Cloud Storage only.",
        )
    # SQLGlot sets ``this`` to ``False`` (not ``None``) when ``AS query`` is
    # absent, so guard on the node type rather than ``is None``.
    inner = tree.args.get("this")
    if not isinstance(inner, exp.Expression):
        raise InvalidQueryError("EXPORT DATA requires an 'AS query_statement'.")
    options = _extract_export_options(tree.args.get("options"))
    return _ExportRequest(options=options, select_sql=inner.sql(dialect="bigquery"))


def write_export(
    arrow_table: pa.Table,
    options: _ExportOptions,
    ctx: AppContext,
) -> _ExportOutcome:
    """Write an already-materialised result to Cloud Storage per ``options``.

    Resolves the destination through the ``gs://`` filesystem shim, applies
    size-based wildcard sharding (``ceil(table.nbytes / threshold)`` files,
    named with BigQuery's 12-digit zero-padded counter), and writes each
    shard via :func:`_copy_relation_to_file`. A wildcard-free URI writes a
    single file and errors if the result exceeds the shard threshold,
    mirroring BigQuery's "use a wildcard for >1 GB" rule.
    """
    uri = options.uri
    wildcards = uri.count("*")
    if wildcards > 1:
        raise InvalidQueryError(
            "EXPORT DATA uri may contain at most one '*' wildcard.",
        )
    threshold = ctx.settings.export_shard_threshold_bytes
    num_rows = arrow_table.num_rows
    nbytes = arrow_table.nbytes

    if wildcards == 0:
        if num_rows > 0 and nbytes > threshold:
            raise InvalidQueryError(
                "Exported data exceeds the single-file size limit; use a uri "
                "with a single '*' wildcard to shard the output across files.",
            )
        shard_count = 1
    elif num_rows == 0:
        shard_count = 1
    else:
        shard_count = max(1, min(num_rows, ceil(nbytes / threshold)))

    written: list[str] = []
    for index, (offset, length) in enumerate(_shard_offsets(num_rows, shard_count)):
        path_uri = uri.replace("*", f"{index:012d}") if wildcards else uri
        resolved = _resolve_uri(path_uri, ctx)
        _validate_local_path(resolved)
        if not options.overwrite and Path(resolved).exists():
            raise InvalidQueryError(
                f"Destination already exists and overwrite is false: {path_uri}",
            )
        _ensure_parent_dir(resolved)
        shard = arrow_table.slice(offset, length)
        view_name = f"_bqemu_export_{uuid4().hex}"
        ctx.engine.connection.register(view_name, shard)
        try:
            _copy_relation_to_file(
                f'SELECT * FROM "{view_name}"',
                resolved,
                options.format,
                ctx,
                header=options.header,
                field_delimiter=options.field_delimiter,
                compression=options.compression,
            )
        finally:
            ctx.engine.connection.unregister(view_name)
        written.append(path_uri)

    return _ExportOutcome(rows=num_rows, file_count=len(written), uris=written)


async def _execute_export_data_job(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    now: Any,
    ctx: AppContext,
    caller: CallerIdentity | None,
) -> JobMeta:
    """Run an ``EXPORT DATA`` statement as a QUERY job and write its result.

    The inner SELECT flows through the standard single-statement pipeline
    (:func:`_run_query_body`) so row-access policies, materialized-view
    refresh, and the other rewrites all apply, then the materialised result
    is written to Cloud Storage. The job reports ``statementType =
    EXPORT_DATA`` with zero result rows.
    """
    request = parse_export_data(bq_sql)
    if request is None:  # pragma: no cover — classifier already guaranteed EXPORT_DATA
        raise InvalidQueryError("Malformed EXPORT DATA statement.")
    effective_caller = caller or CallerIdentity(
        principal="user:anonymous@bqemulator.local",
        is_authenticated=False,
    )
    arrow_table = await _run_query_body(
        project_id=project_id,
        bq_sql=request.select_sql,
        query_params=query_params,
        ctx=ctx,
        is_scripted=False,
        effective_caller=effective_caller,
    )
    outcome = write_export(arrow_table, request.options, ctx)
    JOB_RESULTS[job_id] = _EMPTY_ARROW
    JOB_SCHEMAS[job_id] = []
    ctx.metrics.sql_translation_total.labels(outcome="ok").inc()
    statistics = _build_query_statistics(
        total_rows=0,
        statement_type="EXPORT_DATA",
        num_dml_affected_rows=None,
        export_statistics=(outcome.file_count, outcome.rows),
    )
    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="QUERY",
        state="DONE",
        configuration=_build_query_configuration(bq_sql, project_id, job_id),
        statistics=statistics,
        creation_time=now,
        start_time=now,
        end_time=ctx.clock.now(),
        etag=generate_etag(project_id, job_id, str(now)),
    )


# ---------------------------------------------------------------------------
# Versioning DDL helpers — snapshots, clones, materialized views
# ---------------------------------------------------------------------------


async def _maybe_run_versioning_ddl(
    project_id: str,
    bq_sql: str,
    ctx: AppContext,
) -> pa.Table | None:
    """Detect + execute a Phase-7 DDL; return an empty pa.Table on hit."""
    if not is_versioning_ddl(bq_sql):
        return None
    router = VersioningDDLRouter(project_id)
    parsed = router.parse(bq_sql)
    if parsed is None:
        return None
    await execute_versioning_ddl(parsed, ctx)
    return pa.table({})


# BigQuery ``CREATE ROW ACCESS POLICY`` grammar:
#
#     CREATE [OR REPLACE] ROW ACCESS POLICY [IF NOT EXISTS] <policy>
#       ON <table>
#       [GRANT TO (<grantee_list>)]
#       FILTER USING (<bool_expr>);
#
# Pattern notes:
# * ``IF NOT EXISTS`` is optional (mirrors the DROP form's ``IF EXISTS``).
# * ``GRANT TO`` is optional — BigQuery applies a grantee-less policy to
#   every principal that can query the table; the handler defaults the
#   grantee list accordingly.
# * The policy id and table ref may be backtick-quoted (whole or
#   per-component) and the table ref may carry hyphenated project ids;
#   ``_resolve_table_parts`` strips backticks per component.
# * Callers pass the SQL through ``_strip_trailing_semicolon`` first, so
#   the patterns anchor directly on ``)`` / end-of-string. The grantee
#   and filter captures are *greedy* and have no adjacent ``\s*`` runs:
#   two unbounded whitespace-matching quantifiers over the same input
#   (the old ``.+?\s*\)\s*;?\s*\Z`` tail) is the polynomial-backtracking
#   shape CodeQL flags as ReDoS. ``.+\)`` anchored on ``\Z`` is linear —
#   only the final ``)`` can satisfy ``\)\Z`` — and inner whitespace is
#   trimmed by the handler, not the regex.
_RAP_CREATE_RE = re.compile(
    r"""
    CREATE\s+(?:OR\s+REPLACE\s+)?ROW\s+ACCESS\s+POLICY\s+
    (?:IF\s+NOT\s+EXISTS\s+)?
    `?(?P<policy>[A-Za-z_][A-Za-z0-9_]*)`?\s+
    ON\s+(?P<table>[`\w.-]+)\s+
    (?:GRANT\s+TO\s*\((?P<grantees>[^)]+)\)\s+)?
    FILTER\s+USING\s*\((?P<filter>.+)\)
    \Z
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_RAP_DROP_RE = re.compile(
    r"""
    DROP\s+ROW\s+ACCESS\s+POLICY\s+(?:IF\s+EXISTS\s+)?
    `?(?P<policy>[A-Za-z_][A-Za-z0-9_]*)`?\s+
    ON\s+(?P<table>[`\w.-]+)
    \Z
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _strip_trailing_semicolon(sql: str) -> str:
    """Trim surrounding whitespace and a single trailing ``;`` from ``sql``.

    The RAP DDL detectors anchor on ``)`` / end-of-string with no
    optional trailing-token quantifiers, so the statement terminator is
    normalised here rather than in the patterns (see the note above).
    """
    return sql.strip().removesuffix(";").strip()


_TABLE_REF_THREE_PART = 3
_TABLE_REF_TWO_PART = 2


def _resolve_table_parts(
    raw: str,
    project_id: str,
) -> tuple[str, str, str]:
    """Split a 1/2/3-part backticked-or-bare BigQuery table id."""
    cleaned = raw.strip().strip("`")
    parts = [p.strip("`") for p in cleaned.split(".") if p]
    if len(parts) == _TABLE_REF_THREE_PART:
        return parts[0], parts[1], parts[2]
    if len(parts) == _TABLE_REF_TWO_PART:
        return project_id, parts[0], parts[1]
    raise InvalidQueryError(f"Invalid table reference for RAP DDL: {raw!r}")


def _maybe_run_row_access_ddl(
    project_id: str,
    bq_sql: str,
    ctx: AppContext,
) -> pa.Table | None:
    """Detect + execute ``CREATE / DROP ROW ACCESS POLICY`` DDL.

    ``bq query`` is the canonical CLI for managing RAPs (the REST
    ``rowAccessPolicies`` resource is also supported by the SDK
    clients); BigQuery's ``CREATE ROW ACCESS POLICY`` syntax is:

        CREATE [OR REPLACE] ROW ACCESS POLICY <id>
          ON <table>
          GRANT TO ('<grantee>', '<grantee>', ...)
          FILTER USING (<boolean-expr>);

    and the matching ``DROP ROW ACCESS POLICY <id> ON <table>``.
    """
    sql = _strip_trailing_semicolon(bq_sql)
    create_match = _RAP_CREATE_RE.match(sql)
    drop_match = _RAP_DROP_RE.match(sql)
    if create_match is None and drop_match is None:
        return None
    if create_match is not None:
        proj, ds, tbl = _resolve_table_parts(
            create_match.group("table"),
            project_id,
        )
        grantees_raw = create_match.group("grantees")
        if grantees_raw:
            grantees = tuple(g.strip().strip("'\"") for g in grantees_raw.split(",") if g.strip())
        else:
            # BigQuery applies a policy created without a GRANT TO clause
            # to every principal that can query the table. Default to
            # ``allAuthenticatedUsers`` — the closest emulator analogue of
            # "anyone with query access" (the matcher admits any
            # authenticated caller; the bare empty tuple would match no
            # one, which is the opposite of BigQuery's semantic).
            grantees = ("allAuthenticatedUsers",)
        ctx.row_access.create(
            project_id=proj,
            dataset_id=ds,
            table_id=tbl,
            policy_id=create_match.group("policy"),
            filter_predicate=create_match.group("filter").strip(),
            grantees=grantees,
        )
        return pa.table({})
    assert drop_match is not None  # noqa: S101
    proj, ds, tbl = _resolve_table_parts(
        drop_match.group("table"),
        project_id,
    )
    ctx.row_access.delete(
        project_id=proj,
        dataset_id=ds,
        table_id=tbl,
        policy_id=drop_match.group("policy"),
    )
    return pa.table({})


def _reject_dml_on_immutable(
    project_id: str,
    bq_sql: str,
    ctx: AppContext,
) -> None:
    """Refuse DML targeting a SNAPSHOT or MATERIALIZED_VIEW table.

    BigQuery rejects writes to those table types — the emulator must
    surface the same error so user code that relies on the immutability
    invariant doesn't silently corrupt data.
    """
    import sqlglot

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — non-DML or unparseable; nothing to do
        return
    if not _is_dml(tree):
        return
    for proj, dataset, table in _dml_target_tables(tree, project_id):
        meta = ctx.catalog.get_table(proj, dataset, table)
        if meta is None:
            continue
        if meta.table_type in ("SNAPSHOT", "MATERIALIZED_VIEW"):
            raise InvalidQueryError(
                f"Cannot run DML against {meta.table_type} table "
                f"{proj}.{dataset}.{table}; it is immutable.",
            )


async def _capture_dml_snapshots(
    project_id: str,
    bq_sql: str,
    ctx: AppContext,
) -> None:
    """Snapshot every table modified by ``bq_sql`` (POST-change).

    The snapshot manager publishes ``TableDataChanged`` as a side effect,
    which in turn marks dependent MVs stale. Capture is serialised
    through the write lock so concurrent writers don't interleave a
    half-applied snapshot with a new DML.
    """
    import sqlglot

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — non-DML or unparseable; nothing to do
        return
    if not _is_dml(tree):
        return

    targets = _dml_target_tables(tree, project_id)
    if not targets:
        return

    async with ctx.engine.write_lock():
        for proj, dataset, table in targets:
            table_meta = ctx.catalog.get_table(proj, dataset, table)
            if table_meta is None:
                continue
            # Skip snapshots of snapshot tables and materialized views —
            # they're already point-in-time copies / recomputed artefacts.
            if table_meta.table_type in ("SNAPSHOT", "MATERIALIZED_VIEW"):
                ctx.events.publish(TableDataChanged(proj, dataset, table))
                continue
            ctx.snapshots.record_change(proj, dataset, table)


def _is_dml(tree: Any) -> bool:
    """Return True if ``tree`` is an INSERT/UPDATE/DELETE/MERGE/TRUNCATE."""
    from sqlglot import exp

    # ``TruncateTable`` landed in sqlglot 23+; fall back to the older
    # Command(type="TRUNCATE") node for earlier builds.
    truncate_cls = getattr(exp, "TruncateTable", None)
    dml_types: tuple[type, ...] = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
    if truncate_cls is not None:
        dml_types = (*dml_types, truncate_cls)
    if isinstance(tree, dml_types):
        return True
    if isinstance(tree, exp.Command):
        return str(getattr(tree, "this", "")).upper() == "TRUNCATE"
    return False


# Statement-type values emitted on ``statistics.query.statementType`` —
# matches BigQuery's documented enumeration. The empty string means
# "no classification possible" and the recorder/runner won't write the
# field. Maintained in lock-step with
# ``docs/reference/api-configuration-coverage-matrix.md`` §7.
_DML_STATEMENTS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})

#: Statements whose DuckDB result is a ``Count`` status column that
#: BigQuery surfaces as ``numDmlAffectedRows`` over an empty result
#: set. ``TRUNCATE TABLE`` is DML on the wire — it reports the removed
#: row count and **no** ``ddlOperationPerformed`` (pinned by
#: ``rest_crud/ddl_result_truncate_table``).
_COUNT_TRIMMED_STATEMENTS = _DML_STATEMENTS | {"TRUNCATE_TABLE"}

#: DDL whose job result is fully empty — no schema, no rows (pinned by
#: ``rest_crud/ddl_result_{alter_table_add_column,create_schema,
#: drop_table,drop_view,drop_schema}``).
_EMPTY_RESULT_DDL_STATEMENTS = frozenset(
    {"ALTER_TABLE", "CREATE_SCHEMA", "DROP_TABLE", "DROP_VIEW", "DROP_SCHEMA"},
)

#: DDL whose job result carries the created object's schema with zero
#: rows (pinned by ``rest_crud/ddl_result_create_{table,table_as_select,
#: view}`` and the not-null / complex-types / if-not-exists variants).
_OBJECT_SCHEMA_DDL_STATEMENTS = frozenset(
    {"CREATE_TABLE", "CREATE_TABLE_AS_SELECT", "CREATE_VIEW"},
)


# ``kind`` value → statement-type for CREATE / DROP node dispatch. The
# CREATE_TABLE entry is handled inline because CTAS (CREATE TABLE AS
# SELECT) needs a secondary check on the ``expression`` arg.
_CREATE_KIND_TO_STATEMENT_TYPE = {
    "VIEW": "CREATE_VIEW",
    "FUNCTION": "CREATE_FUNCTION",
    "PROCEDURE": "CREATE_PROCEDURE",
    "SCHEMA": "CREATE_SCHEMA",
    "SNAPSHOT": "CREATE_SNAPSHOT_TABLE",
}
_DROP_KIND_TO_STATEMENT_TYPE = {
    "TABLE": "DROP_TABLE",
    "VIEW": "DROP_VIEW",
    "FUNCTION": "DROP_FUNCTION",
    "PROCEDURE": "DROP_PROCEDURE",
    "SCHEMA": "DROP_SCHEMA",
    "SNAPSHOT": "DROP_SNAPSHOT_TABLE",
}


def _classify_dml(tree: Any, exp_module: Any) -> str:
    """Return the DML ``statementType`` for ``tree``, or ``""`` if not DML.

    Includes the older-sqlglot fallback where ``TRUNCATE`` is parsed
    as ``exp.Command`` rather than the dedicated ``exp.TruncateTable``
    node (the latter was added later in sqlglot's API).
    """
    for node_cls, statement_type in (
        (exp_module.Insert, "INSERT"),
        (exp_module.Update, "UPDATE"),
        (exp_module.Delete, "DELETE"),
        (exp_module.Merge, "MERGE"),
    ):
        if isinstance(tree, node_cls):
            return statement_type
    truncate_cls = getattr(exp_module, "TruncateTable", None)
    if truncate_cls is not None and isinstance(tree, truncate_cls):
        return "TRUNCATE_TABLE"
    if (
        isinstance(tree, exp_module.Command)
        and str(getattr(tree, "this", "")).upper() == "TRUNCATE"
    ):
        return "TRUNCATE_TABLE"
    return ""


def _classify_create(tree: Any) -> str:
    """Return the ``CREATE_*`` statement type for an ``exp.Create`` node."""
    kind = (tree.args.get("kind") or "").upper()
    if kind == "TABLE":
        # ``expression`` carries the SELECT body for CREATE TABLE AS;
        # plain CREATE TABLE has no expression.
        return "CREATE_TABLE_AS_SELECT" if tree.args.get("expression") else "CREATE_TABLE"
    return _CREATE_KIND_TO_STATEMENT_TYPE.get(kind, "")


def _classify_drop(tree: Any) -> str:
    """Return the ``DROP_*`` statement type for an ``exp.Drop`` node."""
    return _DROP_KIND_TO_STATEMENT_TYPE.get((tree.args.get("kind") or "").upper(), "")


def classify_statement_type(bq_sql: str) -> str:
    """Return BigQuery's ``statementType`` for ``bq_sql``.

    Used by the executor to populate ``statistics.query.statementType``
    on every job's response.

    Returns one of: ``SELECT``, ``INSERT``, ``UPDATE``, ``DELETE``,
    ``MERGE``, ``CREATE_TABLE``, ``CREATE_TABLE_AS_SELECT``,
    ``CREATE_VIEW``, ``CREATE_FUNCTION``, ``CREATE_PROCEDURE``,
    ``CREATE_SCHEMA``, ``CREATE_SNAPSHOT_TABLE``, ``DROP_TABLE``,
    ``DROP_VIEW``, ``DROP_FUNCTION``, ``DROP_PROCEDURE``,
    ``DROP_SCHEMA``, ``DROP_SNAPSHOT_TABLE``, ``ALTER_TABLE``,
    ``TRUNCATE_TABLE``, ``CREATE_ROW_ACCESS_POLICY``,
    ``DROP_ROW_ACCESS_POLICY``, ``SCRIPT``, or ``""`` (empty when
    sqlglot cannot parse the SQL — caller writes no ``statementType``
    field in that case).

    Falls back to ``""`` rather than guessing for unparseable input so
    a malformed query doesn't get a misleading classification.
    """
    rap_type = _classify_rap_ddl(bq_sql)
    if rap_type:
        return rap_type
    tree = _parse_for_classification(bq_sql)
    if tree is None:
        return ""
    return _classify_parsed_tree(tree)


def _classify_rap_ddl(bq_sql: str) -> str:
    """Pre-classify ``CREATE/DROP ROW ACCESS POLICY`` DDL via regex.

    ``ROW ACCESS POLICY`` DDL is not in sqlglot's BigQuery grammar — it
    falls back to a generic ``Command`` node — so we classify it via
    the same regexes the executor uses to dispatch the DDL, before the
    parse-based path runs.
    """
    rap_sql = _strip_trailing_semicolon(bq_sql)
    if _RAP_CREATE_RE.match(rap_sql):
        return "CREATE_ROW_ACCESS_POLICY"
    if _RAP_DROP_RE.match(rap_sql):
        return "DROP_ROW_ACCESS_POLICY"
    return ""


def _parse_for_classification(bq_sql: str) -> Any | None:
    """Parse ``bq_sql`` for classification; return ``None`` when SQLGlot can't parse."""
    import sqlglot

    try:
        return sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — best-effort classification
        return None


def _classify_parsed_tree(tree: Any) -> str:
    """Map a SQLGlot AST node to its BigQuery ``statementType`` name.

    Falls through to ``""`` for anything the dispatch doesn't recognise
    so callers can treat the field as "unknown" rather than a wrong
    label.
    """
    from sqlglot import exp

    if isinstance(tree, exp.Export):
        return "EXPORT_DATA"
    dml = _classify_dml(tree, exp)
    if dml:
        return dml
    if isinstance(tree, exp.Select):
        return "SELECT"
    if isinstance(tree, exp.Create):
        return _classify_create(tree)
    if isinstance(tree, exp.Drop):
        return _classify_drop(tree)
    alter_cls = getattr(exp, "AlterTable", None) or getattr(exp, "Alter", None)
    if alter_cls is not None and isinstance(tree, alter_cls):
        return "ALTER_TABLE"
    return ""


def _extract_dml_affected_rows(arrow_table: pa.Table) -> int:
    """Read the ``Count`` column DuckDB returns from a DML statement.

    DuckDB's INSERT / UPDATE / DELETE / MERGE result is a single-row
    table with one column named ``Count`` whose value is the number
    of affected rows. Returns 0 when the table doesn't match that
    shape (defensive — caller decides what to do with 0).
    """
    if arrow_table.num_columns == 0 or arrow_table.num_rows == 0:
        return 0
    first_col = arrow_table.column_names[0]
    if first_col not in {"Count", "count"}:
        return 0
    try:
        return int(arrow_table.column(0)[0].as_py())
    except Exception:  # noqa: BLE001 — defensive fallback
        return 0


# Empty Arrow table — the canonical "no result set" sentinel for DML
# and DDL statements. Real BigQuery returns a 0-column schema + 0 rows
# on INSERT / UPDATE / DELETE / MERGE / TRUNCATE and on every DDL form
# (CREATE TABLE/VIEW additionally report the created object's schema
# via the response-schema override); DuckDB returns a 1-column
# ``Count`` / ``Success`` status table that the finalizer trims here.
_EMPTY_ARROW = pa.table({})


class _DmlTableCollector:
    """Accumulator for the destination tables of a DML statement.

    Centralises the dedup / falsy-coordinate guard so the per-node-type
    collector helpers only express the AST traversal — the
    "is this a complete table ref?" check lives in :meth:`add`.
    """

    __slots__ = ("_out", "_project_id", "_seen")

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._out: list[tuple[str, str, str]] = []
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, table_node: Any) -> None:
        """Record ``table_node`` if it has both dataset + table names; dedup repeats."""
        dataset = table_node.db
        table = table_node.name
        if not dataset or not table:
            return
        proj = table_node.catalog or self._project_id
        key = (proj, dataset, table)
        if key in self._seen:
            return
        self._seen.add(key)
        self._out.append(key)

    def result(self) -> list[tuple[str, str, str]]:
        """Return the collected table refs in insertion order."""
        return list(self._out)


def _dml_target_tables(
    tree: Any,
    project_id: str,
) -> list[tuple[str, str, str]]:
    """Extract the destination table(s) of a DML tree.

    Dispatches per AST node type — INSERT, UPDATE/DELETE/TRUNCATE,
    MERGE, fallback ``Command`` — each routed to a small helper that
    walks the relevant ``this`` / ``expressions`` slots.
    """
    from sqlglot import exp

    collector = _DmlTableCollector(project_id)
    truncate_cls = getattr(exp, "TruncateTable", None)
    if isinstance(tree, exp.Insert):
        _collect_insert_targets(tree, exp, collector)
    elif _is_update_delete_truncate(tree, exp, truncate_cls):
        _collect_update_delete_truncate_targets(tree, exp, collector)
    elif isinstance(tree, exp.Merge):
        _collect_merge_targets(tree, exp, collector)
    elif isinstance(tree, exp.Command):
        _collect_command_targets(tree, collector)
    return collector.result()


def _is_update_delete_truncate(tree: Any, exp_module: Any, truncate_cls: type | None) -> bool:
    """True for UPDATE / DELETE / TRUNCATE-style DML nodes."""
    if isinstance(tree, (exp_module.Update, exp_module.Delete)):
        return True
    return truncate_cls is not None and isinstance(tree, truncate_cls)


def _collect_insert_targets(tree: Any, exp_module: Any, collector: _DmlTableCollector) -> None:
    """Collect the target of ``INSERT [INTO]`` — directly or via ``exp.Schema``."""
    this = tree.this
    if isinstance(this, exp_module.Table):
        collector.add(this)
        return
    if isinstance(this, exp_module.Schema):
        inner = this.this
        if isinstance(inner, exp_module.Table):
            collector.add(inner)


def _collect_update_delete_truncate_targets(
    tree: Any,
    exp_module: Any,
    collector: _DmlTableCollector,
) -> None:
    """Collect the target(s) of UPDATE / DELETE / TRUNCATE statements.

    The primary target lives under ``tree.this``; SQLGlot folds
    multi-table TRUNCATE / DELETE secondary targets into the
    ``expressions`` slot.
    """
    this = tree.this
    if isinstance(this, exp_module.Table):
        collector.add(this)
    for expr in tree.args.get("expressions", []) or []:
        if isinstance(expr, exp_module.Table):
            collector.add(expr)


def _collect_merge_targets(tree: Any, exp_module: Any, collector: _DmlTableCollector) -> None:
    """Collect the destination table of a MERGE statement (always under ``tree.this``)."""
    this = tree.this
    if isinstance(this, exp_module.Table):
        collector.add(this)


def _collect_command_targets(tree: Any, collector: _DmlTableCollector) -> None:
    """Command-fallback TRUNCATE — parse the body for table names."""
    body = str(tree.expression) if tree.expression is not None else ""
    for table_node in _parse_fallback_tables(body):
        collector.add(table_node)


def _parse_fallback_tables(body: str) -> list[Any]:
    """Best-effort parse of a TRUNCATE command's body for table refs."""
    import sqlglot
    from sqlglot import exp as _exp

    try:
        parsed = sqlglot.parse_one(
            f"SELECT * FROM {body.strip().lstrip('TABLE').strip()}",
            read="bigquery",
        )
    except Exception:  # noqa: BLE001
        return []
    return list(parsed.find_all(_exp.Table))


async def _refresh_dependent_mvs(
    project_id: str,
    bq_sql: str,
    ctx: AppContext,
) -> None:
    """Refresh any materialized view this query reads, if stale.

    Walks the BigQuery AST, collects every table reference, and asks
    the MV manager to refresh_if_stale. No-op when the query touches no
    MV.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
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


__all__ = [
    "JOB_RESULTS",
    "JOB_SCHEMAS",
    "build_response_schema",
    "execute_copy_job",
    "execute_extract_job",
    "execute_load_job",
    "execute_query_job",
    "parse_export_data",
    "write_export",
]
