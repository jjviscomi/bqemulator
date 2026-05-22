"""Tables REST routes.

Endpoints:
    GET    /bigquery/v2/projects/{p}/datasets/{d}/tables         — list
    POST   /bigquery/v2/projects/{p}/datasets/{d}/tables         — insert
    GET    /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}     — get
    PATCH  /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}     — patch
    PUT    /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}     — update
    DELETE /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}     — delete

Reference:
    https://cloud.google.com/bigquery/docs/reference/rest/v2/tables
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import (
    Clustering,
    TableFieldSchema,
    TableMeta,
    TableSchema,
    TimePartitioning,
)
from bqemulator.domain.errors import ResourceRef, resource_not_found
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.storage.type_map import bq_schema_to_duckdb_columns

router = APIRouter(prefix="/bigquery/v2", tags=["tables"])

_Ctx = Annotated[AppContext, Depends(get_context)]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _field_to_rest(field: TableFieldSchema) -> dict[str, Any]:
    """Convert a ``TableFieldSchema`` to REST JSON."""
    out: dict[str, Any] = {"name": field.name, "type": field.type, "mode": field.mode}
    if field.description:
        out["description"] = field.description
    if field.fields:
        out["fields"] = [_field_to_rest(f) for f in field.fields]
    if field.range_element_type is not None:
        out["rangeElementType"] = _field_to_rest(field.range_element_type)
    return out


def _table_to_rest(t: TableMeta) -> dict[str, Any]:
    """Serialize a ``TableMeta`` to the BigQuery REST shape."""
    ref = {
        "projectId": t.project_id,
        "datasetId": t.dataset_id,
        "tableId": t.table_id,
    }
    body: dict[str, Any] = {
        "kind": "bigquery#table",
        "id": f"{t.project_id}:{t.dataset_id}.{t.table_id}",
        "tableReference": ref,
        "type": t.table_type,
        "creationTime": str(int(t.creation_time.timestamp() * 1000)),
        "lastModifiedTime": str(int(t.last_modified_time.timestamp() * 1000)),
        "numRows": str(t.num_rows),
        "numBytes": str(t.num_bytes),
        # P7.c follow-up — BigQuery emits four additional byte-count
        # fields on the table resource. The emulator has no
        # logical-vs-physical storage model, so we mirror ``numBytes``
        # across all four. Wire-shape parity for clients that key on
        # the field names; values are not load-bearing.
        "numActiveLogicalBytes": str(t.num_bytes),
        "numTotalLogicalBytes": str(t.num_bytes),
        "numLongTermBytes": "0",
        "numLongTermLogicalBytes": "0",
        "etag": t.etag,
    }
    if t.schema_.fields:
        body["schema"] = {"fields": [_field_to_rest(f) for f in t.schema_.fields]}
    if t.friendly_name:
        body["friendlyName"] = t.friendly_name
    if t.description:
        body["description"] = t.description
    if t.labels:
        body["labels"] = t.labels
    if t.time_partitioning:
        tp = t.time_partitioning
        part_body: dict[str, Any] = {"type": tp.type}
        if tp.field:
            part_body["field"] = tp.field
        if tp.expiration_ms is not None:
            part_body["expirationMs"] = str(tp.expiration_ms)
        if tp.require_partition_filter:
            part_body["requirePartitionFilter"] = True
        body["timePartitioning"] = part_body
    if t.clustering:
        body["clustering"] = {"fields": list(t.clustering.fields)}
    return body


def _table_to_list_item(t: TableMeta) -> dict[str, Any]:
    """Compact table representation for ``tables.list``."""
    return {
        "kind": "bigquery#table",
        "id": f"{t.project_id}:{t.dataset_id}.{t.table_id}",
        "tableReference": {
            "projectId": t.project_id,
            "datasetId": t.dataset_id,
            "tableId": t.table_id,
        },
        "type": t.table_type,
        "creationTime": str(int(t.creation_time.timestamp() * 1000)),
    }


def _parse_schema_fields(raw_fields: list[dict[str, Any]]) -> tuple[TableFieldSchema, ...]:
    """Recursively parse REST schema fields into ``TableFieldSchema``."""
    result: list[TableFieldSchema] = []
    for f in raw_fields:
        sub_fields = tuple(_parse_schema_fields(f["fields"])) if "fields" in f else ()
        range_elem_raw = f.get("rangeElementType")
        range_element_type: TableFieldSchema | None = None
        if isinstance(range_elem_raw, dict) and "type" in range_elem_raw:
            range_element_type = TableFieldSchema(
                # REST shape carries only ``type`` on rangeElementType; the
                # name is internal scaffolding used by the catalog model.
                name=range_elem_raw.get("name", f"{f['name']}__range_element"),
                type=range_elem_raw["type"],
                mode=range_elem_raw.get("mode", "NULLABLE"),
            )
        result.append(
            TableFieldSchema(
                name=f["name"],
                type=f.get("type", "STRING"),
                mode=f.get("mode", "NULLABLE"),
                description=f.get("description"),
                fields=sub_fields,
                range_element_type=range_element_type,
            ),
        )
    return tuple(result)


def _rest_to_table_meta(
    project_id: str,
    dataset_id: str,
    body: dict[str, Any],
    clock: Any,
    existing: TableMeta | None = None,
) -> TableMeta:
    """Build a ``TableMeta`` from a REST request body."""
    ref = body.get("tableReference", {})
    table_id = ref.get("tableId") or body.get("tableId", "")
    now = clock.now()

    schema_raw = body.get("schema", {})
    fields_raw = schema_raw.get("fields", [])
    schema = (
        TableSchema(fields=_parse_schema_fields(fields_raw))
        if fields_raw
        else (existing.schema_ if existing else TableSchema())
    )

    # Parse partitioning config.
    time_part_raw = body.get("timePartitioning")
    time_partitioning = None
    if time_part_raw:
        time_partitioning = TimePartitioning(
            type=time_part_raw.get("type", "DAY"),
            field=time_part_raw.get("field"),
            expiration_ms=time_part_raw.get("expirationMs"),
            require_partition_filter=time_part_raw.get(
                "requirePartitionFilter",
                False,
            ),
        )
    elif existing:
        time_partitioning = existing.time_partitioning

    # Parse clustering config.
    clustering_raw = body.get("clustering")
    clustering = None
    if clustering_raw and "fields" in clustering_raw:
        clustering = Clustering(fields=tuple(clustering_raw["fields"]))
    elif existing:
        clustering = existing.clustering

    view_block = body.get("view") if isinstance(body.get("view"), dict) else None
    view_query = (
        view_block.get("query")
        if view_block is not None
        else (existing.view_query if existing else None)
    )
    # BigQuery infers ``type=VIEW`` when a ``view`` block is provided
    # without an explicit type. Mirror that so client code that uses
    # the official BigQuery client (which doesn't always set ``type``)
    # gets the correct table_type for downstream rewriting.
    inferred_type = "VIEW" if view_query and "type" not in body else None
    table_type = body.get(
        "type",
        inferred_type or (existing.table_type if existing else "TABLE"),
    )

    return TableMeta(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        table_type=table_type,
        schema=schema,
        friendly_name=body.get("friendlyName", existing.friendly_name if existing else None),
        description=body.get("description", existing.description if existing else None),
        labels=body.get("labels", existing.labels if existing else {}),
        time_partitioning=time_partitioning,
        clustering=clustering,
        creation_time=existing.creation_time if existing else now,
        last_modified_time=now,
        num_rows=existing.num_rows if existing else 0,
        num_bytes=existing.num_bytes if existing else 0,
        etag=generate_etag(project_id, dataset_id, table_id, str(now)),
        view_query=view_query,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets/{dataset_id}/tables")
def list_tables(
    project_id: str,
    dataset_id: str,
    ctx: _Ctx,
    maxResults: int = Query(default=1000, alias="maxResults"),  # noqa: N803
) -> dict[str, Any]:
    """List tables in a dataset."""
    tables = ctx.catalog.list_tables(project_id, dataset_id)
    return {
        "kind": "bigquery#tableList",
        "tables": [_table_to_list_item(t) for t in tables[:maxResults]],
        "totalItems": len(tables),
    }


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/tables",
    status_code=status.HTTP_200_OK,
)
async def insert_table(
    project_id: str,
    dataset_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Create a new table."""
    from bqemulator.domain.errors import resource_already_exists

    body = await request.json()
    meta = _rest_to_table_meta(project_id, dataset_id, body, ctx.clock)

    # Check catalog first to give a clean 409 before touching DuckDB.
    if ctx.catalog.get_table(project_id, dataset_id, meta.table_id) is not None:
        raise resource_already_exists(ResourceRef("table", project_id, dataset_id, meta.table_id))

    target_ref = quoted_table_ref(project_id, dataset_id, meta.table_id)
    async with ctx.engine.write_lock():
        # Build DuckDB CREATE TABLE DDL from the schema.
        if meta.schema_.fields:
            fields_raw = [_field_to_rest(f) for f in meta.schema_.fields]
            duckdb_cols = bq_schema_to_duckdb_columns(fields_raw)
            col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in duckdb_cols)
            ctx.engine.execute(f"CREATE TABLE {target_ref} ({col_defs})")
        else:
            # Table with no schema — create empty.
            ctx.engine.execute(
                f"CREATE TABLE {target_ref} (__placeholder INTEGER)",
            )
        created = ctx.catalog.create_table(meta)
    return _table_to_rest(created)


@router.get("/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}")
def get_table(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Get a table by ID.

    Includes a fast path for anonymous query-result tables under the
    reserved ``_bqemu_anonymous`` dataset — these are not registered
    in the catalog; we synthesise the response from ``JOB_RESULTS``
    so dbt-bigquery's post-execution
    ``client.get_table(query_job.destination)`` call has a real
    ``num_rows`` to read for ``SELECT``-style queries.
    """
    if dataset_id == "_bqemu_anonymous" and table_id.startswith("anon"):
        return _anonymous_result_table_to_rest(project_id, dataset_id, table_id)
    t = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if t is None:
        raise resource_not_found(ResourceRef("table", project_id, dataset_id, table_id))
    return _table_to_rest(t)


#: Length of a UUID-4 hex string with hyphens stripped (``32``). Used
#: by :func:`_anonymous_result_table_to_rest` to decide whether to
#: re-insert hyphens before looking the job up in ``JOB_RESULTS``.
_UUID_HEX_LEN = 32


def _anonymous_result_table_to_rest(
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> dict[str, Any]:
    """Synthesise a table-resource response for an anonymous query result.

    The ``table_id`` carries the job-id with hyphens stripped (see
    :func:`bqemulator.jobs.executor._build_query_configuration`); we
    look the rows up directly in
    :data:`bqemulator.jobs.executor.JOB_RESULTS` (a dict keyed by the
    original job-id).
    """
    from bqemulator.jobs.executor import JOB_RESULTS, JOB_SCHEMAS

    # ``table_id`` is ``anon<job_id with hyphens removed>``; rebuild
    # the canonical job-id form by inserting hyphens at UUID positions.
    flat = table_id.removeprefix("anon")
    job_id = flat
    if len(flat) == _UUID_HEX_LEN:
        job_id = f"{flat[0:8]}-{flat[8:12]}-{flat[12:16]}-{flat[16:20]}-{flat[20:32]}"

    arrow_table = JOB_RESULTS.get(job_id)
    schema_fields = JOB_SCHEMAS.get(job_id) or []
    num_rows = arrow_table.num_rows if arrow_table is not None else 0
    return {
        "kind": "bigquery#table",
        "id": f"{project_id}:{dataset_id}.{table_id}",
        "tableReference": {
            "projectId": project_id,
            "datasetId": dataset_id,
            "tableId": table_id,
        },
        "type": "TABLE",
        "numRows": str(num_rows),
        "schema": {"fields": list(schema_fields)},
    }


@router.patch("/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}")
async def patch_table(
    project_id: str,
    dataset_id: str,
    table_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Partial update of a table."""
    existing = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if existing is None:
        raise resource_not_found(ResourceRef("table", project_id, dataset_id, table_id))
    body = await request.json()
    body.setdefault("tableReference", {"tableId": table_id})
    updated = _rest_to_table_meta(project_id, dataset_id, body, ctx.clock, existing)
    result = ctx.catalog.update_table(updated)
    return _table_to_rest(result)


@router.put("/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}")
async def update_table(
    project_id: str,
    dataset_id: str,
    table_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Full replace of a table."""
    existing = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if existing is None:
        raise resource_not_found(ResourceRef("table", project_id, dataset_id, table_id))
    body = await request.json()
    body.setdefault("tableReference", {"tableId": table_id})
    updated = _rest_to_table_meta(project_id, dataset_id, body, ctx.clock, existing)
    result = ctx.catalog.update_table(updated)
    return _table_to_rest(result)


@router.delete(
    "/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_table(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: _Ctx,
) -> Response:
    """Delete a table."""
    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    async with ctx.engine.write_lock():
        ctx.engine.execute(f"DROP TABLE IF EXISTS {target_ref}")
        ctx.catalog.delete_table(project_id, dataset_id, table_id)
        # Phase 7: clean up the AUTO snapshots for this table and any
        # MV-dependency rows — no point in retaining snapshots of a
        # dropped table. USER snapshots survive only if they were
        # already materialised in other datasets.
        ctx.snapshots.drop_snapshots_for_table(
            project_id,
            dataset_id,
            table_id,
            include_user=False,
        )
        ctx.catalog.delete_materialized_view(
            project_id,
            dataset_id,
            table_id,
            not_found_ok=True,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
