"""Jobs REST routes. full lifecycle.

Endpoints:
    POST   /bigquery/v2/projects/{p}/queries              — jobs.query (sync)
    GET    /bigquery/v2/projects/{p}/queries/{jobId}      — getQueryResults
    POST   /bigquery/v2/projects/{p}/jobs                 — jobs.insert
    GET    /bigquery/v2/projects/{p}/jobs                 — jobs.list
    GET    /bigquery/v2/projects/{p}/jobs/{j}             — jobs.get
    POST   /bigquery/v2/projects/{p}/jobs/{j}/cancel      — jobs.cancel
    DELETE /bigquery/v2/projects/{p}/jobs/{j}/delete      — jobs.delete (canonical)
    DELETE /bigquery/v2/projects/{p}/jobs/{j}             — jobs.delete (legacy alias)

Reference:
    https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request

from bqemulator.api.dependencies import AppContext, get_caller, get_context
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import JobMeta, JobType, TableMeta
from bqemulator.domain.errors import (
    AlreadyExistsError,
    DomainError,
    ErrorDetail,
    InvalidQueryError,
    NotFoundError,
    OutOfRangeError,
    PermissionDeniedError,
    ResourceInUseError,
    ResourceRef,
    UnsupportedFeatureError,
    ValidationError,
    resource_not_found,
)
from bqemulator.jobs.ddl_result import DDL_BQ_WIRE_TYPES as _DDL_BQ_WIRE_TYPES
from bqemulator.jobs.executor import (
    JOB_RESULTS,
    JOB_SCHEMAS,
    build_response_schema,
    classify_statement_type,
    execute_copy_job,
    execute_extract_job,
    execute_load_job,
    execute_query_job,
)
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.storage.arrow_bridge import arrow_table_to_bq_rows

if TYPE_CHECKING:
    import pyarrow as pa

router = APIRouter(prefix="/bigquery/v2", tags=["jobs"])

_Ctx = Annotated[AppContext, Depends(get_context)]
_Caller = Annotated[CallerIdentity, Depends(get_caller)]

#: DomainError subclasses that real BigQuery surfaces as
#: ``Job.status.errorResult`` rather than a direct HTTP 4xx response
#: (see ADR 0022 §3 ``Error parity``). The Python client maps
#: ``reason`` → exception class when polling a DONE+ERROR job, so the
#: shape needs to match real BQ. Request-level errors
#: (UnsupportedFeatureError, ValidationError) continue to surface as
#: direct HTTP errors since they are not BigQuery wire-format
#: job-failure semantics — UnsupportedFeatureError is the emulator's
#: out-of-scope marker, ValidationError is request-shape validation.
_SQL_EXECUTION_DOMAIN_ERRORS = (
    NotFoundError,
    AlreadyExistsError,
    PermissionDeniedError,
    InvalidQueryError,
    OutOfRangeError,
    ResourceInUseError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_job_response(
    project_id: str,
    job_id: str,
    job_meta: JobMeta,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build a ``Job`` resource response."""
    statistics: dict[str, Any] = dict(job_meta.statistics or {})
    # BigQuery's ``Job.statistics`` carries millisecond-epoch timestamps
    # for ``creationTime``, ``startTime``, ``endTime``. The ``bq`` CLI
    # crashes ("KeyError: 'creationTime'") when sorting child jobs of a
    # script if ``creationTime`` is absent, so always surface the
    # JobMeta's wall-clock fields here.
    statistics.setdefault(
        "creationTime",
        str(int(job_meta.creation_time.timestamp() * 1000)),
    )
    if job_meta.start_time is not None:
        statistics.setdefault(
            "startTime",
            str(int(job_meta.start_time.timestamp() * 1000)),
        )
    if job_meta.end_time is not None:
        statistics.setdefault(
            "endTime",
            str(int(job_meta.end_time.timestamp() * 1000)),
        )

    result: dict[str, Any] = {
        "kind": "bigquery#job",
        "id": f"{project_id}:{job_id}",
        "jobReference": {"projectId": project_id, "jobId": job_id, "location": "US"},
        "configuration": config,
        "status": {"state": job_meta.state},
        "statistics": statistics,
        "etag": job_meta.etag,
    }
    if job_meta.error_result:
        result["status"]["errorResult"] = job_meta.error_result
        # ``status.errors`` carries the same payload — BQ surfaces both
        # fields and the Python client populates ``exc.errors`` from
        # ``status.errors``. See ADR 0022 §3 (Error parity).
        result["status"]["errors"] = [job_meta.error_result]
    return result


def _domain_error_to_error_result(
    exc: NotFoundError
    | AlreadyExistsError
    | PermissionDeniedError
    | InvalidQueryError
    | OutOfRangeError
    | ResourceInUseError,
) -> dict[str, Any]:
    """Build a BigQuery-shape ``ErrorProto`` dict from a :class:`DomainError`.

    The dict matches the on-the-wire shape BigQuery puts in
    ``Job.status.errorResult``: ``{reason, message, location?}``. The
    Python client maps ``reason`` back to an exception class
    (``notFound`` → :class:`NotFound`, ``duplicate`` →
    :class:`Conflict`, ``invalidQuery`` → :class:`BadRequest`, …) and
    surfaces ``exc.errors[0]`` from this dict, so the conformance
    extractor sees the same shape as a fresh BigQuery client error.
    """
    payload: dict[str, Any] = {
        "reason": exc.bq_reason,
        "message": exc.message,
    }
    # Prefer the explicit ``location`` from the DomainError; fall back
    # to the first detail entry's ``location`` for legacy callers that
    # populated it that way.
    loc = exc.location
    if loc is None and exc.details:
        loc = exc.details[0].location
    if loc is not None:
        payload["location"] = loc
    return payload


def _failed_job_meta(
    project_id: str,
    job_id: str,
    job_type: JobType,
    config: dict[str, Any],
    error_result: dict[str, Any],
    now: datetime,
) -> JobMeta:
    """Build a DONE-state ``JobMeta`` carrying an ``errorResult``.

    When ``execute_query_job`` raises a :class:`DomainError`, the route
    handler converts the exception to this shape so the response is
    HTTP 200 with the job's ``status.errorResult`` populated — the BQ
    client behaviour the conformance fixtures expect. Without this
    conversion the route would return HTTP 409 / 404 / 403 directly,
    and the BQ Python client's retry logic on those codes would hang
    polling for a job that never reached DONE state. See ADR 0022 §3
    (Error parity).
    """
    return JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type=job_type,
        state="DONE",
        configuration=config,
        statistics={"query": {"totalBytesProcessed": "0", "totalRows": "0"}},
        error_result=error_result,
        creation_time=now,
        start_time=now,
        end_time=now,
        etag=generate_etag(project_id, job_id, str(now)),
    )


# ---------------------------------------------------------------------------
# jobs.query (synchronous) — POST /projects/{p}/queries
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/queries")
async def query(
    project_id: str,
    request: Request,
    ctx: _Ctx,
    caller: _Caller,
) -> dict[str, Any]:
    """Execute a synchronous query (jobs.query)."""
    body = await request.json()
    bq_sql: str = body.get("query", "")
    use_legacy_sql: bool = body.get("useLegacySql", False)
    dry_run: bool = body.get("dryRun", False)
    max_results: int = body.get("maxResults", 10000)
    query_params = body.get("queryParameters")
    # Surface a fresh session token when ``createSession=true`` is set
    # on the request body. The synchronous ``jobs.query`` shape accepts
    # the field at the top level (the asynchronous ``jobs.insert``
    # shape places it under ``configuration.query``).
    _validate_session_id(body)
    session_id = _maybe_mint_session(body)

    # When ``useLegacySql=true`` is set, run the narrow legacy-to-
    # standard rewriter that handles the type-cast function subset
    # (INTEGER/FLOAT/STRING/BOOLEAN/BYTES) and the
    # ``[project:dataset.table]`` reference shape. Queries using
    # other legacy-SQL features still surface a translation error
    # downstream, but the simple compat-mode SELECTs work.
    if use_legacy_sql:
        from bqemulator.sql.rewriter.legacy_sql import rewrite_legacy_to_standard

        bq_sql = rewrite_legacy_to_standard(bq_sql)

    job_id = f"bqemu_{uuid4().hex[:12]}"

    if dry_run:
        return await _dry_run_response(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            query_params=query_params,
            ctx=ctx,
            caller=caller,
            wire_shape="query",
        )

    if not bq_sql.strip():
        raise InvalidQueryError("Empty query")

    try:
        job_meta = await execute_query_job(
            project_id,
            job_id,
            bq_sql,
            query_params,
            ctx,
            caller=caller,
        )
    except _SQL_EXECUTION_DOMAIN_ERRORS as exc:
        # ADR 0022 §3: surface SQL execution failures as
        # ``status.errorResult`` on the job rather than a direct HTTP
        # error. This matches real BigQuery's behaviour and is what
        # the BQ Python client expects for jobs.query — without this
        # the client either raises immediately (4xx) or hangs polling
        # (409 retry storm). Request-level errors
        # (UnsupportedFeatureError, ValidationError) continue to
        # surface as direct HTTP responses.
        error_result = _domain_error_to_error_result(exc)
        job_meta = _failed_job_meta(
            project_id=project_id,
            job_id=job_id,
            job_type="QUERY",
            config={"query": {"query": bq_sql}},
            error_result=error_result,
            now=ctx.clock.now(),
        )
        ctx.catalog.upsert_job(job_meta)
        return {
            "kind": "bigquery#queryResponse",
            "jobReference": {"projectId": project_id, "jobId": job_id},
            "jobComplete": True,
            "totalBytesProcessed": "0",
            "schema": {"fields": []},
            "rows": [],
            "totalRows": "0",
            "errors": [error_result],
        }
    ctx.catalog.upsert_job(job_meta)

    arrow_table = JOB_RESULTS[job_id]
    rows = arrow_table_to_bq_rows(arrow_table, limit=max_results)
    schema_fields = JOB_SCHEMAS.get(job_id, [])
    total_rows = arrow_table.num_rows

    response: dict[str, Any] = {
        "kind": "bigquery#queryResponse",
        "jobReference": {"projectId": project_id, "jobId": job_id},
        "jobComplete": True,
        "totalBytesProcessed": "0",
        "schema": {"fields": schema_fields},
        "rows": rows,
        "totalRows": str(total_rows),
    }
    # Surface the response-metadata fields on jobs.query so the BQ
    # Python client populates ``QueryJob.cache_hit``,
    # ``QueryJob.statement_type``, and ``QueryJob.num_dml_affected_rows``.
    # The values come from the executor's ``statistics.query`` block;
    # we mirror them onto the top-level body since the Python client
    # reads both shapes on the synchronous query path.
    _attach_query_metadata(response, job_meta)
    # Attach the minted session token to the response shape and to the
    # persisted JobMeta so async polls find the same id.
    if session_id is not None:
        job_meta.statistics.setdefault("sessionInfo", {"sessionId": session_id})
        response["sessionInfo"] = {"sessionId": session_id}
    if total_rows > max_results:
        response["pageToken"] = str(max_results)
    return response


def _attach_query_metadata(response: dict[str, Any], job_meta: JobMeta) -> None:
    """Copy the executor's ``statistics.query`` fields onto a queryResponse.

    The synchronous ``jobs.query`` endpoint's response body inlines a
    subset of the job's statistics as top-level fields rather than
    nesting under ``statistics``. The BQ Python client's
    ``QueryJob.cache_hit`` / ``.statement_type`` /
    ``.num_dml_affected_rows`` properties read from these top-level
    fields when the job is created via the sync path. See:
    https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query
    """
    query_stats = job_meta.statistics.get("query", {}) if job_meta.statistics else {}
    if "cacheHit" in query_stats:
        response["cacheHit"] = query_stats["cacheHit"]
    if query_stats.get("statementType"):
        response["statementType"] = query_stats["statementType"]
    if query_stats.get("numDmlAffectedRows") is not None:
        response["numDmlAffectedRows"] = query_stats["numDmlAffectedRows"]
    if query_stats.get("ddlOperationPerformed"):
        response["ddlOperationPerformed"] = query_stats["ddlOperationPerformed"]


async def _dry_run_response(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    ctx: AppContext,
    caller: CallerIdentity,
    wire_shape: str,
) -> dict[str, Any]:
    """Build the dry-run response for ``jobs.query`` or ``jobs.insert``.

    Real BigQuery dry-run semantics:
    - For SELECT: return the schema preview (the columns + types the
      query would produce) with 0 rows.
    - For DDL/DML: return a 0-column schema (no projection); the only
      observable side effect is the ``statementType`` classification.
    - ``totalBytesProcessed`` estimate is 0 (the emulator has no cost
      model — see out-of-scope.md ``slot-and-byte-billing-simulation``).
    - The ``job_id`` is omitted from the returned ``jobReference`` for
      ``jobs.query`` dry-runs (no job is persisted); ``jobs.insert``
      dry-runs return a DONE job with the supplied id.

    For SELECT we execute the query to compute the schema then discard
    the rows. The query is run through the same executor pipeline as
    a normal job so RAP / row-access enforcement still applies. For
    DML/DDL we **don't** execute to avoid committing side effects.
    """
    statement_type = classify_statement_type(bq_sql)
    schema_fields: list[dict[str, Any]] = []

    is_destructive = statement_type in _DESTRUCTIVE_STATEMENT_TYPES
    if bq_sql.strip() and not is_destructive:
        # SELECT (or unparseable) — run the query so the executor can
        # compute the result schema. Discard the rows in the response.
        # Dry-run resolver errors surface through the dry-run-specific
        # wire shape: ``error.location="q"`` (single character, not
        # ``"query"``) and the original identifier case from the BQ SQL
        # (DuckDB lowercases identifiers through its parser).
        try:
            job_meta = await execute_query_job(
                project_id,
                job_id,
                bq_sql,
                query_params,
                ctx,
                caller=caller,
            )
        except _SQL_EXECUTION_DOMAIN_ERRORS as exc:
            _rewrite_for_dry_run(exc, bq_sql=bq_sql)
            raise
        schema_fields = JOB_SCHEMAS.get(job_id, [])
        # Drop the recorded result so this dry-run doesn't leak rows
        # to a subsequent ``getQueryResults`` poll.
        JOB_RESULTS.pop(job_id, None)
        JOB_SCHEMAS.pop(job_id, None)
        captured = job_meta.statistics.get("query", {}).get("statementType")
        statement_type = captured or statement_type
    elif is_destructive:
        # Destructive statements skip execution to avoid committing
        # side effects, but BigQuery still reconstructs the schema
        # preview from the DDL column list (CREATE TABLE) or the
        # destination table's catalog entry (INSERT/UPDATE/DELETE/
        # MERGE). Walk the AST in :func:`_destructive_dry_run_schema`
        # to mirror the wire shape without running the statement.
        schema_fields = _destructive_dry_run_schema(
            bq_sql=bq_sql,
            statement_type=statement_type,
            project_id=project_id,
            ctx=ctx,
        )

    body: dict[str, Any] = {
        "kind": "bigquery#queryResponse" if wire_shape == "query" else "bigquery#job",
        "jobComplete": True,
        "totalBytesProcessed": "0",
        "schema": {"fields": schema_fields},
        "rows": [],
        "totalRows": "0",
        "cacheHit": False,
    }
    if statement_type:
        body["statementType"] = statement_type
        # Surface ddlOperationPerformed on the dry-run preview for DDL
        # statements; the comparator's ``_compare_job_metadata`` reads
        # this key when present on the recorded baseline.
        from bqemulator.jobs.ddl_result import ddl_operation_for  # local import to avoid cycle

        ddl_op = ddl_operation_for(statement_type)
        if ddl_op:
            body["ddlOperationPerformed"] = ddl_op
    # ``jobs.query`` dry-run omits the job id; ``jobs.insert`` keeps it.
    body["jobReference"] = (
        {"projectId": project_id}
        if wire_shape == "query"
        else {"projectId": project_id, "jobId": job_id, "location": "US"}
    )
    return body


#: Match ``Function not found: <name> at [L:C]`` where ``<name>`` is
#: an unquoted identifier. The mapper renders this shape with DuckDB's
#: lower-cased identifier; the rewrite below recovers the original
#: casing from the BQ source SQL.
_FUNCTION_NOT_FOUND_RE = re.compile(
    r"Function not found: (?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)


def _rewrite_for_dry_run(exc: DomainError, *, bq_sql: str) -> None:
    """Rewrite a dry-run resolver error to BigQuery's wire-shape envelope.

    Two narrow rewrites are applied in-place on ``exc``:

    1. ``location="query"`` → ``location="q"`` — real BigQuery emits a
       single-character ``location`` field for dry-run resolver errors,
       distinct from the regular ``"query"`` used at runtime.
    2. Identifier-case preservation in ``message`` — DuckDB's parser
       lowercases identifiers before the catalog lookup; the resulting
       ``Function not found: <name>`` message therefore loses the
       caller's casing. The recorded BigQuery baseline carries the
       original case (e.g. ``BQEMU_NONEXISTENT_FUNCTION``), so we scan
       the BQ SQL for any identifier whose lower-cased form matches the
       error's name and substitute back.
    """
    if exc.location == "query":
        exc.location = "q"
    match = _FUNCTION_NOT_FOUND_RE.search(exc.message)
    if match is not None:
        lower_name = match["name"]
        # Find the original-case form of the function name in the BQ SQL.
        # The scan is case-insensitive; the first match wins (functions
        # appear before any string-literal identifier-shaped echo).
        original_case = _recover_identifier_case(bq_sql, lower_name)
        if original_case is not None and original_case != lower_name:
            new_message = exc.message.replace(lower_name, original_case, 1)
            exc.message = new_message
            # Rebuild the args tuple so ``str(exc)`` also reflects the
            # case (the base ``Exception`` stores the original message
            # in ``args``).
            exc.args = (new_message,)


def _recover_identifier_case(bq_sql: str, lower_name: str) -> str | None:
    """Return the first identifier in ``bq_sql`` whose lowercased form matches.

    Scans ``bq_sql`` for tokens that match the ``[A-Za-z_][A-Za-z0-9_]*``
    identifier shape and returns the first one whose lower-cased form
    equals ``lower_name``. Returns ``None`` when no match is found —
    the caller leaves the message untouched in that case.
    """
    pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for token in pattern.findall(bq_sql):
        if token.lower() == lower_name.lower():
            return token
    return None


def _destructive_dry_run_schema(
    *,
    bq_sql: str,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> list[dict[str, Any]]:
    """Reconstruct the dry-run schema preview for a destructive statement.

    BigQuery's dry-run response for CREATE TABLE returns the schema of
    the table that *would* be created; for INSERT/UPDATE/DELETE/MERGE
    it returns the destination table's pre-mutation schema. Both
    paths walk the sqlglot AST to find the relevant column metadata
    without executing the statement.

    Returns an empty list when the AST shape doesn't match a known
    pattern — the caller falls back to the existing 0-col preview.
    """
    import sqlglot

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return []
    if parsed is None:
        return []  # type: ignore[unreachable]

    if statement_type == "CREATE_TABLE":
        return _schema_from_create_table(parsed)

    if statement_type in {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE_TABLE"}:
        table_ref = _destination_table_from_dml(parsed)
        if table_ref is None:
            return []
        meta = ctx.catalog.get_table(
            table_ref[0] or project_id,
            table_ref[1],
            table_ref[2],
        )
        if meta is None:
            return []
        # ``schema_`` is the canonical Python attribute (the Pydantic
        # model aliases it to ``schema`` for JSON serialisation).
        table_schema = getattr(meta, "schema_", None)
        if table_schema is None:
            return []
        return _table_meta_schema_to_response(table_schema)

    return []


def _schema_from_create_table(parsed: Any) -> list[dict[str, Any]]:
    """Extract a BigQuery-shape schema from a sqlglot ``exp.Create`` AST.

    Per-column ``mode`` reflects the DDL's nullability constraint:
    ``REQUIRED`` when the column carries a ``NOT NULL`` constraint
    (SQLGlot ``NotNullColumnConstraint``), ``NULLABLE`` otherwise.
    Closes the gap where ``INFORMATION_SCHEMA.COLUMNS`` returned
    ``is_nullable='YES'`` for every column regardless of the DDL —
    BigQuery returns ``'NO'`` for REQUIRED columns. Pinned by the
    ``information_schema/is_columns_basic`` conformance fixture.
    """
    from sqlglot import expressions as exp

    if not isinstance(parsed, exp.Create):
        return []
    this = parsed.this
    column_defs = this.expressions or [] if isinstance(this, exp.Schema) else []
    fields: list[dict[str, Any]] = []
    for column in column_defs:
        if not isinstance(column, exp.ColumnDef):
            continue
        name = column.name
        if not name:
            continue
        bq_type = _column_def_to_bq_type(column)
        if not bq_type:
            continue
        mode = "REQUIRED" if _has_not_null_constraint(column) else "NULLABLE"
        fields.append({"name": name, "type": bq_type, "mode": mode})
    return fields


def _has_not_null_constraint(column: Any) -> bool:
    """True iff *column*'s SQLGlot ColumnDef carries a NOT NULL constraint."""
    from sqlglot import expressions as exp

    for constraint in column.args.get("constraints") or ():
        if isinstance(constraint, exp.ColumnConstraint) and isinstance(
            constraint.kind, exp.NotNullColumnConstraint
        ):
            return True
    return False


def _column_def_to_bq_type(column: Any) -> str:
    """Map a sqlglot ``exp.ColumnDef``'s data type to a BigQuery wire type name."""
    from sqlglot import expressions as exp

    kind = column.args.get("kind")
    if kind is None:
        return ""
    if isinstance(kind, exp.DataType):
        # SQLGlot canonicalises the BigQuery type names; map back to
        # the wire form via storage.type_map's reverse lookup.
        from bqemulator.storage.type_map import _DUCKDB_TO_BQ as _SQLGLOT_TO_BQ

        sqlglot_name = kind.this.name.upper() if kind.this else ""
        if sqlglot_name in _DDL_BQ_WIRE_TYPES:
            return _DDL_BQ_WIRE_TYPES[sqlglot_name]
        # Fall back to the reverse map in storage.type_map.
        return _SQLGLOT_TO_BQ.get(sqlglot_name, sqlglot_name)
    return ""


def _destination_table_from_dml(parsed: Any) -> tuple[str | None, str, str] | None:
    """Extract the destination table ref (project, dataset, table) from a DML AST."""
    from sqlglot import expressions as exp

    target: Any = None
    if isinstance(parsed, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        target = parsed.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
        return None
    table_name = target.name
    dataset_node = target.args.get("db")
    project_node = target.args.get("catalog")
    if not table_name or not dataset_node:
        return None
    project_id: str | None = (
        project_node.name if project_node is not None and hasattr(project_node, "name") else None
    )
    dataset_id: str = dataset_node.name if hasattr(dataset_node, "name") else str(dataset_node)
    return (project_id, dataset_id, table_name)


def _table_meta_schema_to_response(schema: Any) -> list[dict[str, Any]]:
    """Render a TableMeta-style schema into the BQ REST ``schema.fields`` shape."""
    out: list[dict[str, Any]] = []
    if isinstance(schema, list):
        fields_iter = schema
    elif hasattr(schema, "fields"):
        fields_iter = schema.fields
    else:
        return []
    for field in fields_iter:
        # Field may be a dict-like (REST) or a SchemaField (catalog object).
        name = _field_attr(field, "name")
        field_type = _field_attr(field, "type") or _field_attr(field, "field_type")
        mode = _field_attr(field, "mode") or "NULLABLE"
        if not name or not field_type:
            continue
        out.append({"name": str(name), "type": str(field_type), "mode": str(mode)})
    return out


def _field_attr(field: Any, key: str) -> Any:
    """Read a field attribute by name from a dict or dataclass-like value."""
    if isinstance(field, dict):
        return field.get(key)
    return getattr(field, key, None)


_DESTRUCTIVE_STATEMENT_TYPES = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "TRUNCATE_TABLE",
        "CREATE_TABLE",
        "CREATE_TABLE_AS_SELECT",
        "CREATE_VIEW",
        "CREATE_FUNCTION",
        "CREATE_PROCEDURE",
        "CREATE_SCHEMA",
        "CREATE_SNAPSHOT_TABLE",
        "DROP_TABLE",
        "DROP_VIEW",
        "DROP_FUNCTION",
        "DROP_PROCEDURE",
        "DROP_SCHEMA",
        "DROP_SNAPSHOT_TABLE",
        "ALTER_TABLE",
    },
)


#: In-process catalog of session IDs minted by ``create_session=True``
#: jobs. BigQuery's wire format for session IDs is an opaque
#: base64-encoded protobuf, but clients treat the value as opaque so we
#: mint a URL-safe random token and round-trip it verbatim. The catalog
#: is process-local — sessions don't survive an emulator restart, same
#: as TEMP TABLES and declared variables.
_SESSION_CATALOG: set[str] = set()


def _mint_session_id() -> str:
    """Mint a fresh session_id token and add it to the in-process catalog."""
    token = uuid4().hex
    _SESSION_CATALOG.add(token)
    return token


def _validate_session_id(query_config: dict[str, Any]) -> None:
    """Reject jobs that reference a session_id we did not mint.

    BigQuery returns ``400 invalid: "Invalid input session id."`` when
    ``connectionProperties.session_id`` doesn't refer to an existing
    session. The emulator's session catalog is the only source of truth
    — anything not present in it raises here.
    """
    props = query_config.get("connectionProperties")
    if not isinstance(props, list):
        return
    for entry in props:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") != "session_id":
            continue
        value = entry.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        if value not in _SESSION_CATALOG:
            raise ValidationError("Invalid input session id.")


def _maybe_mint_session(query_config: dict[str, Any]) -> str | None:
    """Mint a fresh session id when ``createSession=true`` and return it.

    The token gets added to :data:`_SESSION_CATALOG` so subsequent jobs
    that reference it via ``connectionProperties.session_id`` survive
    the :func:`_validate_session_id` check. Returns ``None`` when the
    caller did not request a new session.
    """
    if not query_config.get("createSession"):
        return None
    return _mint_session_id()


def _attach_session_info(statistics: dict[str, Any], session_id: str | None) -> None:
    """Surface the minted session token on ``statistics.sessionInfo.sessionId``.

    BigQuery responds to a ``createSession=true`` job by attaching the
    new session's opaque token to ``Job.statistics.sessionInfo.sessionId``
    — the BQ Python client reads that field via
    ``QueryJob.session_info.session_id`` and feeds it into the next
    job's ``connectionProperties``. The emulator mirrors the shape so
    a round-trip client survives unchanged.
    """
    if session_id is None:
        return
    statistics["sessionInfo"] = {"sessionId": session_id}


def _check_schema_update_options(query_config: dict[str, Any]) -> None:
    """Reject ``schemaUpdateOptions`` paired with an unsupported disposition.

    Real BigQuery accepts ``schemaUpdateOptions`` (ALLOW_FIELD_ADDITION /
    ALLOW_FIELD_RELAXATION) only when ``writeDisposition=WRITE_APPEND``,
    or with ``WRITE_TRUNCATE`` on a **table partition** (not the table
    itself). Every other combination surfaces as ``400 invalid: "Schema
    update options should only be specified with WRITE_APPEND
    disposition, or with WRITE_TRUNCATE disposition on a table
    partition."``. The emulator mirrors the rule so clients that rely
    on the rejection (idempotent re-writes, accidental-truncate
    guard-rails) see the same wire shape.
    """
    options = query_config.get("schemaUpdateOptions")
    if not options:
        return
    disposition = (query_config.get("writeDisposition") or "").upper()
    if disposition == "WRITE_APPEND":
        return
    # WRITE_TRUNCATE on a table partition would carry a partition-decorated
    # destination (e.g. ``proj:ds.table$20260101``); the emulator's
    # destination-table parsing doesn't keep the ``$<partition>`` suffix,
    # but real BigQuery only accepts it on partition-level truncates.
    # Without a partition decorator we mirror BQ's stricter rejection.
    raise ValidationError(
        "Schema update options should only be specified with WRITE_APPEND "
        "disposition, or with WRITE_TRUNCATE disposition on a table partition.",
    )


def _clustering_fields(query_config: dict[str, Any]) -> list[str]:
    """Extract the clustering field names from a query configuration."""
    clustering = query_config.get("clustering")
    if not isinstance(clustering, dict):
        return []
    fields = clustering.get("fields")
    if not isinstance(fields, list):
        return []
    return [f for f in fields if isinstance(f, str)]


def _partition_field(query_config: dict[str, Any]) -> str | None:
    """Extract the time-partitioning field name from a query configuration."""
    partitioning = query_config.get("timePartitioning")
    if not isinstance(partitioning, dict):
        return None
    field = partitioning.get("field")
    return field if isinstance(field, str) else None


def _validate_destination_layout_columns(
    bq_sql: str,
    query_config: dict[str, Any],
) -> None:
    """Reject clustering / partitioning column references that are absent from the SELECT.

    Real BigQuery validates ``clusteringFields`` and
    ``timePartitioning.field`` against the SELECT projection at
    submission time and rejects unknown columns with
    ``400 invalid``. ``clusteringFields`` references surface as
    ``Field <name> referenced by clustering field is not found in the
    schema.``; ``timePartitioning.field`` surfaces as
    ``The field specified for partitioning cannot be found in the
    schema.``. The emulator parses the SELECT's output schema via
    sqlglot to recover the projected column names.
    """
    clustering_fields = _clustering_fields(query_config)
    partition_field = _partition_field(query_config)
    if not clustering_fields and not partition_field:
        return
    projected = _projected_column_names(bq_sql)
    if projected is None:
        # AST parse failed — leave validation to the executor.
        return
    for column in clustering_fields:
        if column not in projected:
            raise ValidationError(
                "The field specified for clustering cannot be found in the "
                f"schema. Invalid field: {column}",
            )
    if partition_field and partition_field not in projected:
        raise ValidationError(
            "The field specified for partitioning cannot be found in the schema.",
        )


def _projected_column_names(bq_sql: str) -> set[str] | None:
    """Return the set of column names projected by the outer SELECT.

    Walks the sqlglot AST and pulls every alias / column-name from the
    top-level ``Select.expressions`` list. Returns ``None`` when the
    AST shape isn't a top-level SELECT — the caller skips validation
    in that case rather than risk a false rejection.
    """
    import sqlglot
    from sqlglot import expressions as exp

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return None
    if not isinstance(parsed, exp.Select):
        return None
    names: set[str] = set()
    for projection in parsed.expressions:
        # ``alias`` is set when the projection uses ``AS``; otherwise
        # falls back to the column / function name.
        alias = projection.alias_or_name
        if alias:
            names.add(alias)
    return names


def _apply_default_dataset(
    bq_sql: str,
    query_config: dict[str, Any],
    *,
    request_project_id: str,
) -> str:
    """Pre-translate ``bq_sql`` to qualify unqualified table refs.

    BigQuery's parser uses ``configuration.query.defaultDataset`` to
    qualify any unqualified table reference in the SQL body at
    submission time. The emulator's SQL translator runs after this
    rewrite, so unqualified references survive into DuckDB's binder
    and fail with ``Referenced column / table not found``. The
    closure here rewrites the SQL through the
    :func:`qualify_unqualified_tables` pre-translator before the
    executor sees it.
    """
    default_dataset = query_config.get("defaultDataset")
    if not isinstance(default_dataset, dict):
        return bq_sql
    project_id = default_dataset.get("projectId") or request_project_id
    dataset_id = default_dataset.get("datasetId")
    if not project_id or not dataset_id:
        return bq_sql
    from bqemulator.sql.rewriter.default_dataset import qualify_unqualified_tables

    return qualify_unqualified_tables(
        bq_sql,
        default_project=project_id,
        default_dataset=dataset_id,
    )


def _resolve_write_append_destination(
    query_config: dict[str, Any],
    ctx: AppContext,
    request_project_id: str,
) -> tuple[TableMeta, set[str]] | None:
    """Return ``(destination TableMeta, set of destination column names)`` or None.

    Returns ``None`` whenever any of the destination-resolution
    conditions fail — missing projectId / datasetId / tableId, catalog
    miss, or the destination doesn't carry a schema. The caller short-
    circuits the post-processing in any of those cases.
    """
    destination = query_config.get("destinationTable")
    if not isinstance(destination, dict):
        return None
    project_id = destination.get("projectId") or request_project_id
    dataset_id = destination.get("datasetId")
    table_id = destination.get("tableId")
    if not (project_id and dataset_id and table_id):
        return None
    meta = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if meta is None:
        return None
    dest_table_schema = getattr(meta, "schema_", None)
    if dest_table_schema is None:
        return None
    return meta, {field.name for field in dest_table_schema.fields}


def _validate_write_append_schema(
    arrow_table: Any | None,
    dest_field_names: set[str],
    *,
    allow_field_addition: bool,
) -> list[Any]:
    """Validate the SELECT projection against the destination's schema.

    Returns the list of NEW fields (those not already in the
    destination) — empty unless ``ALLOW_FIELD_ADDITION`` is set. A
    field not present in the destination AND not opted-in via
    ``ALLOW_FIELD_ADDITION`` raises BigQuery's
    ``400 invalid: Invalid schema update. Cannot add fields …`` with
    the FIRST offending field name (BigQuery surfaces them in
    projection order, not alphabetised).
    """
    added_fields: list[Any] = []
    if arrow_table is None:
        return added_fields
    for field in arrow_table.schema:
        if field.name in dest_field_names:
            continue
        if allow_field_addition:
            added_fields.append(field)
            continue
        message = f"Invalid schema update. Cannot add fields (field: {field.name})"
        raise ValidationError(
            message,
            details=[ErrorDetail(reason="invalid", message=message)],
        )
    return added_fields


def _combine_write_append_tables(
    arrow_table: pa.Table | None,
    existing_table: pa.Table,
) -> pa.Table | None:
    """Concat pre-existing rows + SELECT rows, returning the unified table.

    The schemas may differ on column order *and* integer-width (DuckDB
    infers int32 for inline literals; the destination's catalog schema
    is int64). Cast SELECT to the destination's schema so
    ``concat_tables`` accepts both shapes. Returns ``None`` when the
    schemas can't be aligned — the caller treats that as
    "skip the combination."
    """
    import pyarrow as pa

    if arrow_table is None or not arrow_table.num_rows:
        return existing_table
    try:
        aligned = arrow_table.select(existing_table.schema.names)
        aligned = aligned.cast(existing_table.schema)
    except (KeyError, pa.lib.ArrowInvalid):
        return None
    return pa.concat_tables([existing_table, aligned])


async def _apply_write_append(
    *,
    job_meta: JobMeta,
    job_id: str,
    query_config: dict[str, Any],
    ctx: AppContext,
    caller: CallerIdentity,
    request_project_id: str,
) -> JobMeta:
    """Post-process a SELECT-with-destination + ``WRITE_APPEND`` job.

    BigQuery's response for SELECT-with-destination jobs is the
    destination's **post-write** content (pre-existing rows + SELECT
    projection for WRITE_APPEND; just SELECT for WRITE_TRUNCATE). The
    SELECT has already been executed by the caller; this helper
    handles the WRITE_APPEND branch by:

    1. Reading the destination's pre-existing rows from the catalog.
    2. Validating that the SELECT's projection schema is a subset of
       the destination's. If not, raise BigQuery's
       ``400 invalid: Invalid schema update. Cannot add fields …``.
    3. Prepending pre-existing rows to the SELECT result so
       ``JOB_RESULTS[job_id]`` carries the destination's post-write
       content.

    WRITE_TRUNCATE doesn't need a post-processing step — the
    truncate-then-write semantic makes post-write = SELECT, which is
    already what the executor returns.
    """
    if query_config.get("writeDisposition") != "WRITE_APPEND":
        return job_meta
    resolved = _resolve_write_append_destination(query_config, ctx, request_project_id)
    if resolved is None:
        return job_meta
    meta, dest_field_names = resolved

    # Schema-superset rejection — ``ALLOW_FIELD_ADDITION`` bypass
    # routed through ``_validate_write_append_schema``.
    options = {opt.upper() for opt in (query_config.get("schemaUpdateOptions") or [])}
    allow_field_addition = "ALLOW_FIELD_ADDITION" in options
    arrow_table = JOB_RESULTS.get(job_id)
    added_fields = _validate_write_append_schema(
        arrow_table,
        dest_field_names,
        allow_field_addition=allow_field_addition,
    )

    # Read the destination's current content via the executor so
    # the rows match the wire-format encoding used everywhere else.
    project_id, dataset_id, table_id = (
        query_config["destinationTable"].get("projectId") or request_project_id,
        query_config["destinationTable"]["datasetId"],
        query_config["destinationTable"]["tableId"],
    )
    sql = f"SELECT * FROM `{project_id}`.`{dataset_id}`.`{table_id}`"
    readback_job_id = f"_writeappend_readback_{job_id}"
    try:
        await execute_query_job(
            project_id,
            readback_job_id,
            sql,
            query_params=None,
            ctx=ctx,
            caller=caller,
        )
    except _SQL_EXECUTION_DOMAIN_ERRORS:
        return job_meta
    existing_table = JOB_RESULTS.pop(readback_job_id, None)
    JOB_SCHEMAS.pop(readback_job_id, None)
    if existing_table is None:
        return job_meta

    # Pad existing rows + evolve destination schema when
    # ``ALLOW_FIELD_ADDITION`` introduced new columns.
    if added_fields:
        existing_table = _pad_table_with_null_columns(existing_table, added_fields)
        _evolve_destination_schema(ctx=ctx, meta=meta, added_fields=added_fields)

    combined = _combine_write_append_tables(arrow_table, existing_table)
    if combined is None:
        # Schemas couldn't be aligned — comparator will surface the
        # mismatch downstream.
        return job_meta
    JOB_RESULTS[job_id] = combined
    JOB_SCHEMAS[job_id] = build_response_schema(combined.schema)
    return job_meta


def _pad_table_with_null_columns(table: Any, new_fields: list[Any]) -> Any:
    """Append NULL columns named after ``new_fields`` to ``table``.

    BigQuery's ``ALLOW_FIELD_ADDITION`` semantics fill pre-existing
    rows with NULL for the newly-added columns. The arrow concat
    needs both tables to share a schema, so we pad the existing
    table here before the concat.
    """
    import pyarrow as pa

    n = table.num_rows
    for field in new_fields:
        null_array = pa.nulls(n, type=field.type)
        table = table.append_column(field.name, null_array)
    return table


def _evolve_destination_schema(
    *,
    ctx: AppContext,
    meta: Any,
    added_fields: list[Any],
) -> None:
    """Append ``added_fields`` (as NULLABLE) to the destination table's catalog schema.

    Real BigQuery's ``ALLOW_FIELD_ADDITION`` mutates the destination's
    schema in place — future reads see the new columns. The emulator
    mirrors the mutation by composing a new ``TableSchema`` with the
    new fields appended (NULLABLE because the column didn't exist for
    pre-existing rows).
    """
    from bqemulator.catalog.models import TableFieldSchema, TableSchema
    from bqemulator.storage.type_map import _DUCKDB_TO_BQ as _ARROW_TO_BQ

    new_field_models = []
    for field in added_fields:
        # Translate arrow type → BQ wire type via the storage reverse
        # map; fall back to the upper-cased arrow type name.
        arrow_type_name = str(field.type).upper()
        bq_type = _ARROW_TO_BQ.get(arrow_type_name, arrow_type_name)
        new_field_models.append(
            TableFieldSchema(name=field.name, type=bq_type, mode="NULLABLE"),
        )
    existing_fields = tuple(meta.schema_.fields)
    new_schema = TableSchema(fields=existing_fields + tuple(new_field_models))
    new_meta = meta.model_copy(update={"schema_": new_schema})
    ctx.catalog.update_table(new_meta)


def _check_create_disposition(
    query_config: dict[str, Any],
    *,
    ctx: AppContext,
    request_project_id: str,
) -> None:
    """Pre-execution check for ``createDisposition=CREATE_NEVER``.

    Real BigQuery validates the destination table's existence before
    executing the query and rejects with ``404 notFound`` when the
    table does not exist and the disposition forbids creating it.
    Composes with both ``WRITE_TRUNCATE`` and ``WRITE_APPEND``.
    """
    if query_config.get("createDisposition") != "CREATE_NEVER":
        return
    destination = query_config.get("destinationTable")
    if not isinstance(destination, dict):
        return
    project_id = destination.get("projectId") or request_project_id
    dataset_id = destination.get("datasetId")
    table_id = destination.get("tableId")
    if not (project_id and dataset_id and table_id):
        return
    if ctx.catalog.get_table(project_id, dataset_id, table_id) is None:
        # BigQuery's wire shape for "table not found" errors:
        #   ``Not found: Table <project>:<dataset>.<table>``
        # (capital T; colon between project and dataset; dot between
        # dataset and table). ``resource_not_found`` produces a
        # different shape (``Not found: table:<project>.<dataset>.<resource>``)
        # which doesn't match the table-not-found wire format, so we
        # build the ``NotFoundError`` directly.
        message = f"Not found: Table {project_id}:{dataset_id}.{table_id}"
        raise NotFoundError(
            message,
            details=[ErrorDetail(reason="notFound", message=message)],
        )


async def _build_dry_run_query_response(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    session_id: str | None,
    config: dict[str, Any],
    ctx: AppContext,
    caller: CallerIdentity,
    now: datetime,
) -> dict[str, Any]:
    """Build the dry-run response for the ``query`` job branch.

    Generates the preview via :func:`_dry_run_response`, builds a settled
    DONE :class:`JobMeta` whose statistics derive from the preview,
    persists it, and returns the wire response with the schema preview
    attached.
    """
    preview = await _dry_run_response(
        project_id=project_id,
        job_id=job_id,
        bq_sql=bq_sql,
        query_params=query_params,
        ctx=ctx,
        caller=caller,
        wire_shape="insert",
    )
    statistics: dict[str, Any] = {
        "query": {
            "totalBytesProcessed": "0",
            "totalRows": "0",
            "cacheHit": False,
        },
    }
    if preview.get("statementType"):
        statistics["query"]["statementType"] = preview["statementType"]
    # Propagate ddlOperationPerformed so the recorded job_metadata diff
    # for DDL dry-runs has the same key as a real BigQuery dry-run preview.
    if preview.get("ddlOperationPerformed"):
        statistics["query"]["ddlOperationPerformed"] = preview["ddlOperationPerformed"]
    job_meta = JobMeta(
        project_id=project_id,
        job_id=job_id,
        job_type="QUERY",
        state="DONE",
        configuration=config,
        statistics=statistics,
        creation_time=now,
        start_time=now,
        end_time=now,
        etag=generate_etag(project_id, job_id, str(now)),
    )
    # Even on a dry-run, BigQuery surfaces the minted session token on
    # the response so the client can chain a subsequent (non-dry-run)
    # job that references it.
    _attach_session_info(job_meta.statistics, session_id)
    ctx.catalog.upsert_job(job_meta)
    response = _build_job_response(project_id, job_id, job_meta, config)
    # Attach the schema preview so a ``jobs.getQueryResults`` poll could
    # surface it (rare; most dry-run callers use the sync ``jobs.query``
    # endpoint).
    response["statistics"]["query"]["schema"] = {"fields": preview["schema"]["fields"]}
    return response


async def _execute_query_or_failed_meta(
    *,
    project_id: str,
    job_id: str,
    bq_sql: str,
    query_params: list[dict[str, Any]] | None,
    query_config: dict[str, Any],
    config: dict[str, Any],
    ctx: AppContext,
    caller: CallerIdentity,
) -> JobMeta:
    """Run ``execute_query_job`` + ``_apply_write_append``; persist failures via the async envelope.

    ADR 0022 §3: SQL execution failures are persisted on the job's
    ``status.errorResult`` so the BQ Python client's retry logic on
    POST /jobs (which retries on 409 / 503) sees a settled DONE+ERROR
    state instead of polling for a never-completing job. Request-level
    errors (UnsupportedFeatureError, ValidationError) continue to
    surface as direct HTTP responses.
    """
    try:
        job_meta = await execute_query_job(
            project_id,
            job_id,
            bq_sql,
            query_params,
            ctx,
            caller=caller,
        )
    except _SQL_EXECUTION_DOMAIN_ERRORS as exc:
        return _failed_job_meta(
            project_id=project_id,
            job_id=job_id,
            job_type="QUERY",
            config=config,
            error_result=_domain_error_to_error_result(exc),
            now=ctx.clock.now(),
        )
    # SELECT-with-destination + WRITE_APPEND. BigQuery returns the
    # destination's post-write content (pre-existing rows + SELECT rows)
    # and enforces schema-superset rejection. Run AFTER the SELECT so we
    # can compare its projection schema to the destination's.
    # WRITE_TRUNCATE's wire shape happens to equal the SELECT projection
    # (BQ truncates then writes, so post-write = SELECT) so the existing
    # path covers it.
    return await _apply_write_append(
        job_meta=job_meta,
        job_id=job_id,
        query_config=query_config,
        ctx=ctx,
        caller=caller,
        request_project_id=project_id,
    )


async def _handle_query_job(
    project_id: str,
    job_id: str,
    config: dict[str, Any],
    *,
    dry_run: bool,
    ctx: AppContext,
    caller: CallerIdentity,
    now: datetime,
) -> JobMeta | dict[str, Any]:
    """Handle the ``"query"`` branch of :func:`insert_job`.

    Pre-validates the request, optionally rewrites legacy SQL, and then
    either dispatches to the dry-run builder or executes the query.

    Returns one of:

    * a fully-built dry-run response (``dict``) — the caller returns it
      directly; the helper already persisted the dry-run :class:`JobMeta`.
    * a non-dry-run :class:`JobMeta` with session info attached — the
      caller runs the shared upsert + response-build path.
    """
    query_config = config["query"]
    bq_sql = query_config.get("query", "")
    use_legacy_sql = query_config.get("useLegacySql", False)
    query_params = query_config.get("queryParameters")
    # Pre-execution validations match real BigQuery's submission-time
    # rejections. These run before the dry-run / execution path so an
    # invalid request gets the documented error envelope without
    # touching the executor or persisting a job.
    _validate_session_id(query_config)
    # schemaUpdateOptions are only legal with WRITE_APPEND (or
    # WRITE_TRUNCATE on a partition decorator).
    _check_schema_update_options(query_config)
    # Clustering / partitioning column references must exist on the
    # SELECT projection.
    _validate_destination_layout_columns(bq_sql, query_config)
    # Mint a session token if ``createSession=true``; the token will be
    # attached to ``statistics.sessionInfo.sessionId`` on the response so
    # the client can route follow-up jobs to the same session via
    # ``connectionProperties.session_id``.
    session_id = _maybe_mint_session(query_config)
    if not dry_run:
        _check_create_disposition(query_config, ctx=ctx, request_project_id=project_id)
    # Apply ``defaultDataset`` to unqualified table refs in the SQL
    # body. BigQuery's parser does this at submission time; the
    # emulator's translator doesn't have access to the job-level config,
    # so we rewrite before handing the SQL to the executor.
    bq_sql = _apply_default_dataset(bq_sql, query_config, request_project_id=project_id)
    # Narrow legacy-to-standard SQL rewriter for the ``useLegacySql=true``
    # compat-mode case (see the matching branch in ``query`` above for
    # the rationale).
    if use_legacy_sql:
        from bqemulator.sql.rewriter.legacy_sql import rewrite_legacy_to_standard

        bq_sql = rewrite_legacy_to_standard(bq_sql)
    if dry_run:
        return await _build_dry_run_query_response(
            project_id=project_id,
            job_id=job_id,
            bq_sql=bq_sql,
            query_params=query_params,
            session_id=session_id,
            config=config,
            ctx=ctx,
            caller=caller,
            now=now,
        )
    job_meta = await _execute_query_or_failed_meta(
        project_id=project_id,
        job_id=job_id,
        bq_sql=bq_sql,
        query_params=query_params,
        query_config=query_config,
        config=config,
        ctx=ctx,
        caller=caller,
    )
    # Surface the session token on the JobMeta's statistics for both the
    # success path and the error-result path so a subsequent ``jobs.get``
    # poll finds the same field shape the ``jobs.insert`` synchronous
    # response carried.
    _attach_session_info(job_meta.statistics, session_id)
    return job_meta


async def _handle_load_job_with_async_envelope(
    project_id: str,
    job_id: str,
    config: dict[str, Any],
    ctx: AppContext,
) -> JobMeta:
    """Run ``execute_load_job``, wrapping engine-level errors in an async-error envelope.

    Source-file processing errors return HTTP 200 with the job's
    ``status.errorResult`` populated rather than a 5xx. Validation
    errors (:class:`UnsupportedFeatureError`, :class:`InvalidQueryError`
    for ``Unknown source format ...``, missing destination,
    :class:`ValidationError`) still bubble out as direct HTTP responses
    so existing tests and the AVRO/ORC missing-extension fallback keep
    their 400 / 501 contracts. Engine-level exceptions (DuckDB
    conversion / IO / binder errors, fastavro decode errors) get the
    async envelope treatment.
    """
    try:
        return await execute_load_job(project_id, job_id, config, ctx)
    except (UnsupportedFeatureError, InvalidQueryError, ValidationError):
        raise
    except Exception as exc:  # noqa: BLE001 — engine error → async envelope
        return _failed_job_meta(
            project_id=project_id,
            job_id=job_id,
            job_type="LOAD",
            config=config,
            error_result={
                "reason": "invalid",
                "message": f"Error while reading data, error message: {exc}",
                "location": config.get("load", {}).get("sourceUris", [""])[0],
            },
            now=ctx.clock.now(),
        )


# ---------------------------------------------------------------------------
# jobs.insert — POST /projects/{p}/jobs
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/jobs")
async def insert_job(
    project_id: str,
    request: Request,
    ctx: _Ctx,
    caller: _Caller,
) -> dict[str, Any]:
    """Create and execute a job (query, load, extract, or copy)."""
    body = await request.json()
    config = body.get("configuration", {})
    job_ref = body.get("jobReference", {})
    job_id = job_ref.get("jobId") or f"bqemu_{uuid4().hex[:12]}"
    dry_run = config.get("dryRun", False)
    now = ctx.clock.now()

    # Dispatch on the job-type configuration key. The query branch may
    # short-circuit with a fully-built dry-run response; every other
    # branch produces a ``JobMeta`` that flows through the shared
    # persist + response-build tail below.
    if "query" in config:
        result = await _handle_query_job(
            project_id,
            job_id,
            config,
            dry_run=dry_run,
            ctx=ctx,
            caller=caller,
            now=now,
        )
        if isinstance(result, dict):
            return result
        job_meta = result
    elif "load" in config:
        job_meta = await _handle_load_job_with_async_envelope(
            project_id,
            job_id,
            config,
            ctx,
        )
    elif "extract" in config:
        job_meta = await execute_extract_job(project_id, job_id, config, ctx)
    elif "copy" in config:
        job_meta = await execute_copy_job(project_id, job_id, config, ctx)
    else:
        raise InvalidQueryError(
            "Job configuration must contain one of: query, load, extract, copy",
        )

    ctx.catalog.upsert_job(job_meta)
    # Merge any executor-enriched configuration (e.g. the
    # ``destinationTable`` populated by ``_build_query_configuration``
    # in ``jobs.executor``) over the request's original config so
    # client-side fields (``useLegacySql``, ``defaultDataset``, etc.)
    # survive while the executor's additions still reach the wire.
    _merge_executor_config(config, job_meta.configuration)
    return _build_job_response(project_id, job_id, job_meta, config)


def _merge_executor_config(
    request_config: dict[str, Any],
    executor_config: dict[str, Any] | None,
) -> None:
    """Overlay executor-set ``configuration`` keys onto the request's config.

    Currently only the ``query.destinationTable`` slot is propagated —
    the executor sets this for every successful QUERY job so REST
    clients that fetch the destination metadata post-execution (e.g.
    dbt-bigquery's ``client.get_table(query_job.destination)``) see a
    real ref instead of ``None``. Mutates ``request_config`` in place.
    """
    if not executor_config:
        return
    exec_query = executor_config.get("query") or {}
    dest = exec_query.get("destinationTable")
    if not dest:
        return
    request_query = request_config.setdefault("query", {})
    request_query.setdefault("destinationTable", dest)


# ---------------------------------------------------------------------------
# jobs.list — GET /projects/{p}/jobs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/jobs")
def list_jobs(
    project_id: str,
    ctx: _Ctx,
    maxResults: int = Query(default=100, alias="maxResults"),  # noqa: N803
    stateFilter: str | None = Query(default=None, alias="stateFilter"),  # noqa: N803
    parentJobId: str | None = Query(default=None, alias="parentJobId"),  # noqa: N803
) -> dict[str, Any]:
    """List jobs in a project.

    ``stateFilter`` accepts BigQuery's lowercase-keyword form
    (``pending`` / ``running`` / ``done``); the in-memory catalog
    stores the uppercase form, so the route uppercases the filter
    before dispatching.

    ``parentJobId`` is bq's child-job lookup for scripts. The emulator
    runs script statements in-process inside the parent ``execute_query_job``
    rather than emitting per-statement child jobs, so this lookup
    always returns an empty list — which causes ``bq query`` to fall
    back to printing the parent's results (the desired behaviour).
    """
    if parentJobId is not None:
        return {"kind": "bigquery#jobList", "jobs": [], "totalItems": 0}
    normalised_filter: str | None = stateFilter.upper() if stateFilter else None
    jobs = ctx.catalog.list_jobs(
        project_id,
        state_filter=normalised_filter,
        max_results=maxResults,
    )
    return {
        "kind": "bigquery#jobList",
        "jobs": [
            {
                "kind": "bigquery#job",
                "id": f"{j.project_id}:{j.job_id}",
                "jobReference": {
                    "projectId": j.project_id,
                    "jobId": j.job_id,
                    "location": "US",
                },
                "status": {"state": j.state},
                "configuration": j.configuration,
                "statistics": _job_stats_with_timestamps(j),
            }
            for j in jobs
        ],
        "totalItems": len(jobs),
    }


def _job_stats_with_timestamps(j: JobMeta) -> dict[str, Any]:
    """Render a ``JobMeta``'s statistics with timestamps always populated.

    Specifically, ``creationTime`` / ``startTime`` / ``endTime`` are
    always present. The ``bq`` CLI sorts script child jobs by
    ``statistics.creationTime`` and crashes with ``KeyError`` if the
    field is absent.
    """
    statistics: dict[str, Any] = dict(j.statistics or {})
    statistics.setdefault(
        "creationTime",
        str(int(j.creation_time.timestamp() * 1000)),
    )
    if j.start_time is not None:
        statistics.setdefault(
            "startTime",
            str(int(j.start_time.timestamp() * 1000)),
        )
    if j.end_time is not None:
        statistics.setdefault(
            "endTime",
            str(int(j.end_time.timestamp() * 1000)),
        )
    return statistics


# ---------------------------------------------------------------------------
# jobs.get — GET /projects/{p}/jobs/{j}
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/jobs/{job_id}")
def get_job(
    project_id: str,
    job_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Get a job by ID."""
    job_meta = ctx.catalog.get_job(project_id, job_id)
    if job_meta is None:
        raise resource_not_found(ResourceRef("job", project_id, resource_id=job_id))
    return _build_job_response(project_id, job_id, job_meta, job_meta.configuration)


# ---------------------------------------------------------------------------
# jobs.cancel — POST /projects/{p}/jobs/{j}/cancel
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(
    project_id: str,
    job_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Cancel a job.

    In the emulator, jobs execute synchronously — they are always DONE
    by the time the client can call cancel. This endpoint returns the
    job's current state.
    """
    job_meta = ctx.catalog.get_job(project_id, job_id)
    if job_meta is None:
        raise resource_not_found(ResourceRef("job", project_id, resource_id=job_id))
    return {
        "kind": "bigquery#jobCancelResponse",
        "job": _build_job_response(project_id, job_id, job_meta, job_meta.configuration),
    }


# ---------------------------------------------------------------------------
# jobs.delete — DELETE /projects/{p}/jobs/{j}/delete (canonical BQ wire path)
#               DELETE /projects/{p}/jobs/{j}        (back-compat alias)
# ---------------------------------------------------------------------------


@router.delete("/projects/{project_id}/jobs/{job_id}/delete")
def delete_job_canonical(
    project_id: str,
    job_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Delete a job (canonical BigQuery REST API path with ``/delete`` suffix).

    See https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/delete.
    Real BigQuery returns ``200 OK`` with an empty JSON body (``{}``)
    on the ``/delete``-suffixed path. The :func:`delete_job` alias
    below carries the short form for back-compat.
    """
    ctx.catalog.delete_job(project_id, job_id)
    JOB_RESULTS.pop(job_id, None)
    JOB_SCHEMAS.pop(job_id, None)
    return {}


@router.delete("/projects/{project_id}/jobs/{job_id}")
def delete_job(
    project_id: str,
    job_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Delete a job (back-compat alias for the canonical ``/delete``-suffixed path)."""
    ctx.catalog.delete_job(project_id, job_id)
    JOB_RESULTS.pop(job_id, None)
    JOB_SCHEMAS.pop(job_id, None)
    return {}


# ---------------------------------------------------------------------------
# getQueryResults — GET /projects/{p}/queries/{j}
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/queries/{job_id}")
def get_query_results(
    project_id: str,
    job_id: str,
    ctx: _Ctx,  # noqa: ARG001 — required for auth context
    maxResults: int = Query(default=10000, alias="maxResults"),  # noqa: N803
    startIndex: int = Query(default=0, alias="startIndex"),  # noqa: N803
    pageToken: str | None = Query(default=None, alias="pageToken"),  # noqa: N803
) -> dict[str, Any]:
    """Fetch paginated results for a completed query job."""
    arrow_table = JOB_RESULTS.get(job_id)
    if arrow_table is None:
        raise resource_not_found(ResourceRef("job", project_id, resource_id=job_id))

    offset = int(pageToken) if pageToken else startIndex
    rows = arrow_table_to_bq_rows(arrow_table, offset=offset, limit=maxResults)
    schema_fields = JOB_SCHEMAS.get(job_id, [])
    total_rows = arrow_table.num_rows

    response: dict[str, Any] = {
        "kind": "bigquery#getQueryResultsResponse",
        "jobReference": {"projectId": project_id, "jobId": job_id},
        "jobComplete": True,
        "totalBytesProcessed": "0",
        "schema": {"fields": schema_fields},
        "rows": rows,
        "totalRows": str(total_rows),
    }
    next_offset = offset + maxResults
    if next_offset < total_rows:
        response["pageToken"] = str(next_offset)
    return response
