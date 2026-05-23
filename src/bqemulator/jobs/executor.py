"""Job executor — dispatches job commands asynchronously.

The executor manages a bounded semaphore to cap concurrent jobs and
routes each job type to its command implementation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from bqemulator.catalog.ddl_sync import sync_created_table, sync_created_view
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
from bqemulator.jobs.error_mapper import translate_runtime_error
from bqemulator.jobs.orc_reader import read_orc_to_arrow
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


def _arrow_type_to_bq_type(arrow_type: pa.DataType) -> str:
    """Map a pyarrow scalar type to a BigQuery type name for response schemas.

    For LIST types, returns the BigQuery type of the *element*. The
    REPEATED mode is recorded separately by :func:`_arrow_field_to_schema_entry`
    so the wire format matches BigQuery's
    ``{type: <elem>, mode: REPEATED}`` shape.
    """
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        return _arrow_type_to_bq_type(arrow_type.value_type)
    # ADR 0023 §1.B: DuckDB's ``SIGN(INT)`` returns ``TINYINT`` (Arrow
    # ``int8``) and several smaller-width arithmetic shortcuts emit
    # ``SMALLINT`` (``int16``). Both map to BigQuery's ``INTEGER`` on the
    # wire — the unsigned variants likewise. Without these cases the
    # fallback would surface the column as ``STRING``.
    if pa.types.is_integer(arrow_type):
        return "INTEGER"
    if pa.types.is_float64(arrow_type) or pa.types.is_float32(arrow_type):
        return "FLOAT"
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "STRING"
    if pa.types.is_timestamp(arrow_type):
        return "TIMESTAMP" if arrow_type.tz else "DATETIME"
    if pa.types.is_date(arrow_type):
        return "DATE"
    if pa.types.is_time(arrow_type):
        return "TIME"
    # ADR 0023 §1.G: ``MonthDayNano`` interval Arrow type → ``INTERVAL``
    # on the wire. Without this branch DuckDB's INTERVAL columns
    # surface as the STRING fallback (the value renderer already
    # produces a canonical ``Y-M D H:M:S`` string).
    if pa.types.is_interval(arrow_type):
        return "INTERVAL"
    if pa.types.is_decimal(arrow_type):
        # ADR 0023 §1.B: BigQuery's NUMERIC has fixed scale 9 and
        # BIGNUMERIC carries scale up to 38. Any DECIMAL whose scale
        # exceeds 9 originated from a BIGNUMERIC literal / column /
        # ``bqemu_to_bignumeric`` marker — surface it as BIGNUMERIC on
        # the wire so the schema matches BigQuery's recorded baseline.
        scale = getattr(arrow_type, "scale", 0) or 0
        if scale > 9:  # noqa: PLR2004 — BigQuery NUMERIC scale boundary
            return "BIGNUMERIC"
        return "NUMERIC"
    if pa.types.is_binary(arrow_type):
        return "BYTES"
    if pa.types.is_struct(arrow_type):
        return "RECORD"
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

    # P3.a / ADR 0022 §3: scripting-lexer errors (``Unterminated string
    # literal`` etc.) are raised directly as InvalidQueryError; route
    # them through the mapper so the BigQuery-shape wording (``Syntax
    # error: Unclosed string literal at [L:C]``) is surfaced before the
    # error reaches the route handler.
    try:
        script = parse_script(bq_sql)
    except DomainError as exc:
        translated = translate_runtime_error(exc)
        if translated is exc:
            raise
        raise translated from exc
    is_scripted = len(script.statements) != 1 or not isinstance(
        script.statements[0],
        SqlStmt,
    )
    script_statement_count = len(script.statements)

    # P7.b — classify the statement type up front so the response
    # populates ``statistics.query.statementType`` consistently across
    # the versioning-DDL fast path, the scripting path, and the
    # legacy single-SQL path. Best-effort: unparseable input returns
    # ``""`` and the field is omitted.
    statement_type = "SCRIPT" if is_scripted else classify_statement_type(bq_sql)

    # Intercept versioning DDL (CREATE SNAPSHOT / CLONE / MATERIALIZED
    # VIEW) and row-access-policy DDL before the SQL translator ever
    # sees them. Only applies to single-statement input — multi-
    # statement scripts dispatch these per-statement inside
    # ``ScriptInterpreter`` (ADR 0023 §1.F) so the matching regexes
    # don't greedy-match across statement boundaries.
    if not is_scripted:
        rap_result = _maybe_run_row_access_ddl(project_id, bq_sql, ctx)
        if rap_result is not None:
            arrow_table = rap_result
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
                    statement_type=statement_type or "CREATE_ROW_ACCESS_POLICY",
                    num_dml_affected_rows=None,
                ),
                creation_time=now,
                start_time=now,
                end_time=ctx.clock.now(),
                etag=generate_etag(project_id, job_id, str(now)),
            )
        ddl_result = await _maybe_run_versioning_ddl(project_id, bq_sql, ctx)
        if ddl_result is not None:
            arrow_table = ddl_result
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
                    statement_type=statement_type or "CREATE_SNAPSHOT_TABLE",
                    num_dml_affected_rows=None,
                ),
                creation_time=now,
                start_time=now,
                end_time=ctx.clock.now(),
                etag=generate_etag(project_id, job_id, str(now)),
            )

    # Refuse DML against immutable table types (SNAPSHOT,
    # MATERIALIZED_VIEW). BigQuery treats these as read-only.
    _reject_dml_on_immutable(project_id, bq_sql, ctx)

    effective_caller = caller or CallerIdentity(
        principal="user:anonymous@bqemulator.local",
        is_authenticated=False,
    )
    if is_scripted:
        interpreter = ScriptInterpreter(ctx, project_id, caller=effective_caller)
        arrow_table = await _run_script(interpreter, bq_sql, ctx)
    else:
        arrow_table = await _run_single_sql(
            project_id,
            bq_sql,
            query_params,
            ctx,
            caller=effective_caller,
        )
        # ADR 0023 §1.F — register plain ``CREATE [OR REPLACE] TABLE``
        # outputs in the catalog so downstream lookups (versioning
        # managers, INFORMATION_SCHEMA) find them. MATERIALIZED VIEW,
        # CLONE, and SNAPSHOT forms route through dedicated managers
        # and are not synced here.
        sync_created_table(bq_sql, project_id, ctx)
        # ADR 0018 (revised 2026-05-19) — register plain ``CREATE
        # [OR REPLACE] VIEW`` outputs in the catalog so the row-access
        # rewriter's ``_expand_view`` branch can recurse through the
        # view body and apply caller-bound policies on the base
        # tables it references. Closes the ``rap_filter_via_view``
        # conformance fixture.
        sync_created_view(bq_sql, project_id, ctx)

    # Capture snapshots for tables modified by this statement, and
    # propagate TableDataChanged so dependent MVs flip stale.
    await _capture_dml_snapshots(project_id, bq_sql, ctx)

    # P7.b — DML statements return a 1-column ``Count`` table from
    # DuckDB; real BigQuery returns a 0-column schema + 0 rows + a
    # ``numDmlAffectedRows`` statistic. Strip the Count column for the
    # wire-format response and capture the affected-rows count for
    # the statistics payload.
    num_dml_affected_rows: int | None = None
    if statement_type in _DML_STATEMENTS:
        num_dml_affected_rows = _extract_dml_affected_rows(arrow_table)
        arrow_table = _EMPTY_ARROW

    JOB_RESULTS[job_id] = arrow_table
    schema_fields = build_response_schema(arrow_table.schema)
    JOB_SCHEMAS[job_id] = schema_fields

    ctx.metrics.sql_translation_total.labels(outcome="ok").inc()

    statistics = _build_query_statistics(
        total_rows=arrow_table.num_rows,
        statement_type=statement_type,
        num_dml_affected_rows=num_dml_affected_rows,
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
) -> dict[str, Any]:
    """Construct the canonical ``statistics`` dict for a query job.

    Always populates ``cacheHit=False`` (the emulator has no query
    cache, so real BigQuery's `False`-by-default is what every fresh
    query returns). ``statementType`` is omitted when the classifier
    returned ``""`` (unparseable). ``numDmlAffectedRows`` is included
    only for DML statements. ``ddlOperationPerformed`` is set for the
    DDL statement types catalogued in
    :data:`_DDL_OPERATION_BY_STATEMENT`.

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
        ddl_op = _ddl_operation_for(statement_type)
        if ddl_op:
            query_stats["ddlOperationPerformed"] = ddl_op
    if num_dml_affected_rows is not None:
        query_stats["numDmlAffectedRows"] = str(num_dml_affected_rows)
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
    translate_result = _translator.translate(bq_sql, schema=schema_dict or None)
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
        # P3.a / ADR 0022 §3: pre-execution domain errors (RAP denials,
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


async def execute_load_job(  # noqa: PLR0915 — linear format dispatch, ADR 0027
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
    load_config = config.get("load", {})

    dest_table = load_config.get("destinationTable", {})
    dest_project = dest_table.get("projectId", project_id)
    dest_dataset = dest_table.get("datasetId", "")
    dest_table_id = dest_table.get("tableId", "")
    source_uris = load_config.get("sourceUris", [])
    source_format = load_config.get("sourceFormat", "CSV").upper()
    write_disposition = load_config.get("writeDisposition", "WRITE_APPEND")
    create_disposition = load_config.get("createDisposition", "CREATE_IF_NEEDED")

    target_ref = quoted_table_ref(dest_project, dest_dataset, dest_table_id)

    # CREATE_IF_NEEDED — materialise the destination from the explicit
    # schema (bq CLI / SDK clients pass ``load.schema.fields``) before
    # touching DuckDB. CREATE_NEVER + missing table → notFound. If a
    # schema is not supplied (autodetect path), the existing DuckDB
    # COPY/INSERT call below will raise a binder error which the load
    # error wrapper translates to a proper ``invalid`` job error.
    if (
        create_disposition == "CREATE_IF_NEEDED"
        and ctx.catalog.get_table(dest_project, dest_dataset, dest_table_id) is None
    ):
        _maybe_create_load_destination(
            dest_project=dest_project,
            dest_dataset=dest_dataset,
            dest_table_id=dest_table_id,
            load_config=load_config,
            now=now,
            ctx=ctx,
        )

    # Resolve URIs: gs:// → local path under GCS_LOCAL_ROOT, or file:// → local.
    resolved_paths = [_resolve_uri(uri, ctx) for uri in source_uris]
    for path in resolved_paths:
        _validate_local_path(path)

    async with ctx.engine.write_lock():
        # Handle write disposition.
        if write_disposition == "WRITE_TRUNCATE":
            ctx.engine.execute(f"DELETE FROM {target_ref}")
        elif write_disposition == "WRITE_EMPTY":
            count = ctx.engine.execute(
                f"SELECT COUNT(*) FROM {target_ref}",
            ).fetchone()
            if count and count[0] > 0:
                raise InvalidQueryError(
                    f"Table {dest_dataset}.{dest_table_id} is not empty and "
                    "writeDisposition is WRITE_EMPTY",
                )

        for path in resolved_paths:
            # DuckDB accepts ? placeholders for file paths in COPY and
            # read_* functions, so we parameterise rather than string-
            # concatenate to shut the door on path-injection (even though
            # _validate_local_path already vetted the value).
            if source_format == "CSV":
                ctx.engine.execute(
                    f"COPY {target_ref} FROM ? (FORMAT CSV, HEADER)",
                    [path],
                )
            elif source_format in ("NEWLINE_DELIMITED_JSON", "JSON"):
                ctx.engine.execute(
                    f"COPY {target_ref} FROM ? (FORMAT JSON)",
                    [path],
                )
            elif source_format == "PARQUET":
                ctx.engine.execute(
                    f"INSERT INTO {target_ref} SELECT * FROM read_parquet(?)",
                    [path],
                )
            elif source_format == "AVRO":
                # DuckDB's ``avro`` extension provides ``read_avro``. It is
                # loaded at engine boot (best-effort) via
                # :meth:`DuckDBEngine._load_format_extensions`; if loading
                # failed the SELECT below raises a ``Table Function with
                # name read_avro does not exist`` catalog error, which we
                # surface back to the client as an UnsupportedFeatureError.
                # G1-follow-up (2026-05-20): when the Avro file uses the
                # ``decimal`` logical type, DuckDB returns the column as
                # BLOB and the auto-cast to NUMERIC fails — pre-detect
                # this via the writer schema and route through the
                # fastavro fallback (which decodes ``decimal`` to Python
                # ``Decimal`` directly). All other Avro shapes stay on
                # the fast DuckDB path. Any other failure (missing file,
                # genuine schema mismatch) bubbles through error_mapper
                # unchanged and is converted to a DONE-with-errorResult
                # JobMeta by the outer wrapper below.
                if is_decimal_logical_avro(path):
                    arrow_table = read_avro_to_arrow(path)
                    ctx.engine.connection.register("_bqemu_avro_load", arrow_table)
                    try:
                        ctx.engine.execute(
                            f"INSERT INTO {target_ref} SELECT * FROM _bqemu_avro_load",
                        )
                    finally:
                        ctx.engine.connection.unregister("_bqemu_avro_load")
                else:
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
            elif source_format == "ORC":
                arrow_table = read_orc_to_arrow(path)
                # arrow_scan reads from a Python-side Arrow table the
                # caller binds at execution time. We register a temporary
                # view via ``register`` (DuckDB's relation API) so the
                # INSERT picks up the schema correctly.
                ctx.engine.connection.register("_bqemu_orc_load", arrow_table)
                try:
                    ctx.engine.execute(
                        f"INSERT INTO {target_ref} SELECT * FROM _bqemu_orc_load",
                    )
                finally:
                    ctx.engine.connection.unregister("_bqemu_orc_load")
            else:
                raise InvalidQueryError(f"Unknown source format: {source_format}")

    # Update row count.
    count_result = ctx.engine.execute(
        f"SELECT COUNT(*) FROM {target_ref}",
    ).fetchone()
    new_count = count_result[0] if count_result else 0

    table_meta = ctx.catalog.get_table(dest_project, dest_dataset, dest_table_id)
    if table_meta is not None:
        ctx.catalog.update_table(table_meta.model_copy(update={"num_rows": new_count}))
        # Capture a snapshot + notify dependents. The load path
        # already released its write lock; reacquire for the snapshot CTAS.
        async with ctx.engine.write_lock():
            ctx.snapshots.record_change(dest_project, dest_dataset, dest_table_id)

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

    # DuckDB's COPY TO needs the path as a literal; we've already whitelisted
    # it via ``_validate_local_path`` to ensure no quotes or shell escapes
    # can smuggle into the literal.
    if dest_format == "CSV":
        ctx.engine.execute(
            f"COPY ({select_sql}) TO '{dest_path}' (FORMAT CSV, HEADER)",
        )
    elif dest_format == "PARQUET":
        ctx.engine.execute(
            f"COPY ({select_sql}) TO '{dest_path}' (FORMAT PARQUET)",
        )
    elif dest_format in ("NEWLINE_DELIMITED_JSON", "JSON"):
        ctx.engine.execute(
            f"COPY ({select_sql}) TO '{dest_path}' (FORMAT JSON)",
        )
    elif dest_format == "AVRO":
        # G1: DuckDB's ``avro`` extension supports ``COPY ... TO ...
        # (FORMAT AVRO)`` natively. The extension is loaded best-effort
        # at engine boot; if absent COPY raises a ``COPY ... FORMAT
        # 'avro' not supported`` error which we surface back to the
        # client as an UnsupportedFeatureError. Other failures (e.g.
        # path validation, write-time schema issues) bubble unchanged.
        try:
            ctx.engine.execute(
                f"COPY ({select_sql}) TO '{dest_path}' (FORMAT AVRO)",
            )
        except Exception as exc:
            if _is_missing_extension_error(exc, "avro"):
                raise UnsupportedFeatureError(
                    "Extract to AVRO requires DuckDB's ``avro`` extension. "
                    "Re-enable BQEMU_ENABLE_FORMAT_EXTENSIONS or run the "
                    "emulator with network access to extensions.duckdb.org.",
                ) from exc
            raise
    else:
        raise InvalidQueryError(f"Unknown destination format: {dest_format}")

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
    from bqemulator.catalog.models import TableMeta, TableSchema

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
        else:
            if write_disposition == "WRITE_TRUNCATE":
                ctx.engine.execute(f"DELETE FROM {dst_ref}")
            elif write_disposition == "WRITE_EMPTY":
                existing = ctx.engine.execute(
                    f"SELECT COUNT(*) FROM {dst_ref}",
                ).fetchone()
                if existing and existing[0] > 0:
                    raise InvalidQueryError(
                        f"Table {dst_ds}.{dst_table_id} is not empty and "
                        "writeDisposition is WRITE_EMPTY",
                    )
            ctx.engine.execute(f"INSERT INTO {dst_ref} SELECT * FROM {src_ref}")

        ctx.snapshots.record_change(dst_proj, dst_ds, dst_table_id)


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


_RAP_CREATE_RE = re.compile(
    r"""
    \s*CREATE\s+(?:OR\s+REPLACE\s+)?ROW\s+ACCESS\s+POLICY\s+
    (?P<policy>[A-Za-z_][A-Za-z0-9_]*)\s+
    ON\s+(?P<table>`?[^`\s(]+`?)\s+
    GRANT\s+TO\s*\(\s*(?P<grantees>[^)]+)\s*\)\s+
    FILTER\s+USING\s*\(\s*(?P<filter>.+?)\s*\)\s*;?\s*\Z
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_RAP_DROP_RE = re.compile(
    r"""
    \s*DROP\s+ROW\s+ACCESS\s+POLICY\s+(?:IF\s+EXISTS\s+)?
    (?P<policy>[A-Za-z_][A-Za-z0-9_]*)\s+
    ON\s+(?P<table>`?[^`\s(]+`?)\s*;?\s*\Z
    """,
    re.IGNORECASE | re.VERBOSE,
)


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
    create_match = _RAP_CREATE_RE.match(bq_sql)
    drop_match = _RAP_DROP_RE.match(bq_sql)
    if create_match is None and drop_match is None:
        return None
    if create_match is not None:
        proj, ds, tbl = _resolve_table_parts(
            create_match.group("table"),
            project_id,
        )
        grantees_raw = create_match.group("grantees")
        grantees = tuple(g.strip().strip("'\"") for g in grantees_raw.split(",") if g.strip())
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
_DDL_OPERATION_BY_STATEMENT = {
    "CREATE_TABLE": "CREATE",
    "CREATE_TABLE_AS_SELECT": "CREATE",
    "CREATE_VIEW": "CREATE",
    "CREATE_FUNCTION": "CREATE",
    "CREATE_PROCEDURE": "CREATE",
    "CREATE_SCHEMA": "CREATE",
    "CREATE_SNAPSHOT_TABLE": "CREATE",
    "DROP_TABLE": "DROP",
    "DROP_VIEW": "DROP",
    "DROP_FUNCTION": "DROP",
    "DROP_PROCEDURE": "DROP",
    "DROP_SCHEMA": "DROP",
    "DROP_SNAPSHOT_TABLE": "DROP",
    "ALTER_TABLE": "ALTER",
    "TRUNCATE_TABLE": "TRUNCATE",
}
_DML_STATEMENTS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})


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
    on every job's response (P7.b — closes the
    ``api_configuration/*`` conformance divergences surfaced by P7.a).

    Returns one of: ``SELECT``, ``INSERT``, ``UPDATE``, ``DELETE``,
    ``MERGE``, ``CREATE_TABLE``, ``CREATE_TABLE_AS_SELECT``,
    ``CREATE_VIEW``, ``CREATE_FUNCTION``, ``CREATE_PROCEDURE``,
    ``CREATE_SCHEMA``, ``CREATE_SNAPSHOT_TABLE``, ``DROP_TABLE``,
    ``DROP_VIEW``, ``DROP_FUNCTION``, ``DROP_PROCEDURE``,
    ``DROP_SCHEMA``, ``DROP_SNAPSHOT_TABLE``, ``ALTER_TABLE``,
    ``TRUNCATE_TABLE``, ``SCRIPT``, or ``""`` (empty when sqlglot
    cannot parse the SQL — caller writes no ``statementType`` field
    in that case).

    Falls back to ``""`` rather than guessing for unparseable input so
    a malformed query doesn't get a misleading classification.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — best-effort classification
        return ""

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


def _ddl_operation_for(statement_type: str) -> str:
    """Return the ``ddlOperationPerformed`` value for a DDL statement type.

    Returns ``""`` for non-DDL statements so the caller skips writing
    the field.
    """
    return _DDL_OPERATION_BY_STATEMENT.get(statement_type, "")


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


# Empty Arrow table — used as the canonical "no result set" sentinel
# for DML statements. Real BigQuery returns a 0-column schema + 0 rows
# on INSERT / UPDATE / DELETE / MERGE; DuckDB returns a 1-column
# ``Count`` table that the runner trims here.
_EMPTY_ARROW = pa.table({})


def _dml_target_tables(
    tree: Any,
    project_id: str,
) -> list[tuple[str, str, str]]:
    """Extract the destination table(s) of a DML tree."""
    from sqlglot import exp

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add_from_table_node(table_node: exp.Table) -> None:
        dataset = table_node.db
        table = table_node.name
        proj = table_node.catalog or project_id
        if not dataset or not table:
            return
        key = (proj, dataset, table)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    truncate_cls = getattr(exp, "TruncateTable", None)
    if isinstance(tree, exp.Insert):
        this = tree.this
        if isinstance(this, exp.Table):
            _add_from_table_node(this)
        elif isinstance(this, exp.Schema):
            inner = this.this
            if isinstance(inner, exp.Table):
                _add_from_table_node(inner)
    elif isinstance(tree, (exp.Update, exp.Delete)) or (
        truncate_cls is not None and isinstance(tree, truncate_cls)
    ):
        this = tree.this
        if isinstance(this, exp.Table):
            _add_from_table_node(this)
        for expr in tree.args.get("expressions", []) or []:
            if isinstance(expr, exp.Table):
                _add_from_table_node(expr)
    elif isinstance(tree, exp.Merge):
        this = tree.this
        if isinstance(this, exp.Table):
            _add_from_table_node(this)
    elif isinstance(tree, exp.Command):
        # Command-fallback TRUNCATE: parse the body for the table name.
        body = str(tree.expression) if tree.expression is not None else ""
        for table_node in _parse_fallback_tables(body):
            _add_from_table_node(table_node)
    return out


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
]
