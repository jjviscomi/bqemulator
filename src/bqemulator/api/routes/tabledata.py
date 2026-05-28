"""TableData REST routes.

Endpoints:
    POST /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}/insertAll
    GET  /bigquery/v2/projects/{p}/datasets/{d}/tables/{t}/data

Reference:
    https://cloud.google.com/bigquery/docs/reference/rest/v2/tabledata
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
import pyarrow as pa

from bqemulator.api.dependencies import AppContext, get_context
from bqemulator.domain.errors import ResourceRef, resource_not_found
from bqemulator.storage.arrow_bridge import arrow_table_to_bq_rows, bq_rows_to_arrow
from bqemulator.storage.sql_identifiers import quoted_table_ref, register_name

router = APIRouter(prefix="/bigquery/v2", tags=["tabledata"])

_Ctx = Annotated[AppContext, Depends(get_context)]


def _build_arrow_schema(fields_raw: list[dict[str, Any]]) -> pa.Schema:
    """Build a pyarrow schema from BigQuery REST schema fields.

    Specialized BigQuery types are stamped with metadata so the
    arrow_bridge coercer can dispatch on the BigQuery type when
    converting JSON input. GEOGRAPHY backs onto ``pa.string()`` and
    carries ``bq_type=GEOGRAPHY`` so the coercer turns inbound WKT
    into hex-encoded WKB; INTERVAL uses Arrow's native
    ``month_day_nano_interval`` and accepts BigQuery interval strings;
    RANGE expands into a struct of the element type.
    """
    pa_fields: list[pa.Field] = []
    for f in fields_raw:
        bq_type = f.get("type", "STRING").upper()
        mode = f.get("mode", "NULLABLE")
        name = f["name"]
        metadata: dict[bytes, bytes] = {b"bq_type": bq_type.encode("ascii")}

        if bq_type in ("RECORD", "STRUCT"):
            sub_schema = _build_arrow_schema(f.get("fields", []))
            arrow_type: pa.DataType = pa.struct(sub_schema)
        elif bq_type == "RANGE":
            range_elem = f.get("rangeElementType") or f.get("range_element_type") or {}
            elem_type = (range_elem.get("type") or "DATE").upper()
            inner = _bq_type_to_arrow(elem_type)
            arrow_type = pa.struct(
                [pa.field("start", inner), pa.field("end", inner)],
            )
        elif bq_type == "GEOGRAPHY":
            # WKB hex string carrier — see arrow_bridge._wkt_to_wkb_hex.
            arrow_type = pa.string()
        else:
            arrow_type = _bq_type_to_arrow(bq_type)

        if mode == "REPEATED":
            arrow_type = pa.list_(arrow_type)

        pa_fields.append(
            pa.field(
                name,
                arrow_type,
                nullable=(mode != "REQUIRED"),
                metadata=metadata,
            ),
        )
    return pa.schema(pa_fields)


def _bq_type_to_arrow(bq_type: str) -> pa.DataType:
    """Map a scalar BigQuery type name to a pyarrow DataType."""
    mapping: dict[str, pa.DataType] = {
        "INT64": pa.int64(),
        "INTEGER": pa.int64(),
        "FLOAT64": pa.float64(),
        "FLOAT": pa.float64(),
        "NUMERIC": pa.decimal128(38, 9),
        "BIGNUMERIC": pa.decimal256(76, 38),
        "BOOL": pa.bool_(),
        "BOOLEAN": pa.bool_(),
        "STRING": pa.string(),
        "BYTES": pa.binary(),
        "DATE": pa.date32(),
        "TIME": pa.time64("us"),
        "DATETIME": pa.timestamp("us"),
        "TIMESTAMP": pa.timestamp("us", tz="UTC"),
        "JSON": pa.string(),  # JSON stored as string in Arrow
        "INTERVAL": pa.month_day_nano_interval(),
        "GEOGRAPHY": pa.string(),  # WKB hex carrier
    }
    return mapping.get(bq_type.upper(), pa.string())


def _has_geography_column(table_meta_fields: list[Any]) -> bool:
    """Return True if any field in *table_meta_fields* is GEOGRAPHY (recursive)."""
    for field in table_meta_fields:
        if str(field.type).upper() == "GEOGRAPHY":
            return True
        if field.fields and _has_geography_column(list(field.fields)):
            return True
    return False


def _build_insert_select(table_meta_fields: list[Any], reg_name: str) -> str:
    """Build the SELECT clause for INSERT INTO ... SELECT <projection> FROM <reg>.

    Plain columns project as-is. GEOGRAPHY columns project through
    ``ST_GeomFromHEXWKB(col)`` because the inbound Arrow column carries
    a hex-encoded WKB string and DuckDB does not auto-cast BLOB →
    GEOMETRY.
    """
    parts: list[str] = []
    for field in table_meta_fields:
        col = f'{reg_name}."{field.name}"'
        if str(field.type).upper() == "GEOGRAPHY":
            parts.append(f'ST_GeomFromHEXWKB({col}) AS "{field.name}"')
        else:
            parts.append(f'{col} AS "{field.name}"')
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# insertAll
# ---------------------------------------------------------------------------


def _fields_raw_from_schema(fields: Any) -> list[dict[str, Any]]:
    """Project a ``TableSchema.fields`` tuple onto :func:`_build_arrow_schema`'s shape."""
    return [
        {
            "name": f.name,
            "type": f.type,
            "mode": f.mode,
            **(
                {"rangeElementType": {"type": f.range_element_type.type}}
                if f.range_element_type is not None
                else {}
            ),
        }
        for f in fields
    ]


def _partition_rows_for_insert(
    rows: list[dict[str, Any]],
    arrow_schema: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ``rows`` into ``(good, errors)`` by attempted per-row Arrow conversion.

    Implements the ``skipInvalidRows=true`` partial-success contract:
    each row's conversion is attempted in isolation; failures are
    captured in the returned error list (formatted by
    :func:`_format_insert_error`) instead of aborting the request.
    """
    good_rows: list[dict[str, Any]] = []
    insert_errors: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        try:
            bq_rows_to_arrow([row], arrow_schema)
        except (ValueError, TypeError) as exc:
            insert_errors.append(_format_insert_error(idx, exc, row, arrow_schema))
            continue
        good_rows.append(row)
    return good_rows, insert_errors


@router.post("/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/insertAll")
async def insert_all(
    project_id: str,
    dataset_id: str,
    table_id: str,
    request: Request,
    ctx: _Ctx,
) -> dict[str, Any]:
    """Stream rows into a table via the legacy insertAll endpoint."""
    table_meta = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if table_meta is None:
        raise resource_not_found(ResourceRef("table", project_id, dataset_id, table_id))

    body = await request.json()
    rows = body.get("rows", [])
    skip_invalid_rows = bool(body.get("skipInvalidRows", False))
    if not rows:
        return {"kind": "bigquery#tableDataInsertAllResponse", "insertErrors": []}

    arrow_schema = _build_arrow_schema(_fields_raw_from_schema(table_meta.schema_.fields))

    # ``skipInvalidRows=true`` requests partial success — convert each
    # row in isolation and capture per-row failures in ``insertErrors[]``
    # (matching BigQuery's wire shape). Without the flag, the first bad
    # row aborts the whole request with an internal error.
    insert_errors: list[dict[str, Any]] = []
    if skip_invalid_rows:
        rows, insert_errors = _partition_rows_for_insert(rows, arrow_schema)
        if not rows:
            return {
                "kind": "bigquery#tableDataInsertAllResponse",
                "insertErrors": insert_errors,
            }
    arrow_table = bq_rows_to_arrow(rows, arrow_schema)

    from uuid import uuid4

    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    reg_name = register_name(f"__bqemu_insertall_{uuid4().hex[:12]}")
    schema_fields = list(table_meta.schema_.fields)
    select_projection = (
        _build_insert_select(schema_fields, reg_name)
        if _has_geography_column(schema_fields)
        else "*"
    )
    async with ctx.engine.write_lock():
        ctx.engine.connection.register(reg_name, arrow_table)
        try:
            ctx.engine.execute(
                f"INSERT INTO {target_ref} SELECT {select_projection} FROM {reg_name}",
            )
        finally:
            ctx.engine.connection.unregister(reg_name)

        # Update row count in catalog.
        new_count = table_meta.num_rows + arrow_table.num_rows
        updated = table_meta.model_copy(update={"num_rows": new_count})
        ctx.catalog.update_table(updated)

        # Capture a snapshot and emit TableDataChanged.
        # ``snapshots.record_change`` publishes the event; subscribers
        # are required to be idempotent.
        ctx.snapshots.record_change(project_id, dataset_id, table_id)

    return {
        "kind": "bigquery#tableDataInsertAllResponse",
        "insertErrors": insert_errors,
    }


def _format_insert_error(
    idx: int,
    _exc: BaseException,
    row: dict[str, Any],
    arrow_schema: Any,
) -> dict[str, Any]:
    """Format a per-row conversion error into BigQuery's ``insertErrors[]`` shape.

    Real BigQuery returns ``{index, errors: [{reason, location, debugInfo,
    message}, ...]}`` per failing row. The emulator's best-effort
    location is the first column name from the request's ``json``
    payload — clients use this to highlight the offending field.

    ``_exc`` is part of the contract (the raised conversion exception)
    but is intentionally not consulted in the body — its text is not
    echoed to the wire to avoid surfacing internal exception details
    (CodeQL ``py/stack-trace-exposure``).
    """
    location = ""
    bad_value: Any = None
    payload = row.get("json") if isinstance(row, dict) else None
    if isinstance(payload, dict):
        # Best-effort: pick the first json column whose name appears in
        # the table's schema. This is the column the conversion likely
        # tripped on (the converter walks columns in schema order).
        schema_columns = {field.name for field in arrow_schema}
        for key, value in payload.items():
            if key in schema_columns:
                location = key
                bad_value = value
                break

    # BigQuery's error wording is ``Cannot convert value to <type>
    # (bad value): <value>``. Rebuild the message in that shape using
    # the failing field's declared type + the offending value. When the
    # offending column can't be identified, fall back to a sanitised
    # message — the ``index`` in the returned dict already lets clients
    # correlate the failure with the input row. The raw exception text
    # is intentionally NOT echoed to the wire to avoid surfacing
    # internal exception details (CodeQL ``py/stack-trace-exposure``).
    message = "Cannot convert row to the table's schema"
    if location:
        try:
            field_type = arrow_schema.field(location).type
        except (KeyError, IndexError):  # pragma: no cover - defensive
            field_type = None
        if field_type is not None:
            type_name = _bq_type_name_for(field_type)
            message = f"Cannot convert value to {type_name} (bad value): {bad_value}"
    return {
        "index": idx,
        "errors": [
            {
                "reason": "invalid",
                "location": location,
                "debugInfo": "",
                "message": message,
            },
        ],
    }


def _bq_type_name_for(arrow_type: Any) -> str:
    """Map a pyarrow type to BigQuery's user-facing type name.

    The mapping covers the primitive types the emulator's insertAll
    converter rejects; the fallback name is the arrow type's
    ``str(...)``.
    """
    name = str(arrow_type)
    return _ARROW_TO_BQ_USER_NAME.get(name, name)


_ARROW_TO_BQ_USER_NAME: dict[str, str] = {
    "int64": "integer",
    "int32": "integer",
    "int16": "integer",
    "int8": "integer",
    "float": "float",
    "double": "float",
    "bool": "boolean",
    "string": "string",
}


# ---------------------------------------------------------------------------
# list (tabledata.list — paginated row read)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/data")
def list_tabledata(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: _Ctx,
    maxResults: int = Query(default=10000, alias="maxResults"),  # noqa: N803
    startIndex: int = Query(default=0, alias="startIndex"),  # noqa: N803
    pageToken: str | None = Query(default=None, alias="pageToken"),  # noqa: N803
    selectedFields: str | None = Query(default=None, alias="selectedFields"),  # noqa: N803
) -> dict[str, Any]:
    """Read rows from a table (paginated).

    Supports BigQuery's ``selectedFields`` (CSV column projection) and
    ``pageToken`` (opaque continuation token). The emulator's
    ``pageToken`` is a literal stringified offset — opaque to clients,
    cheap to compute, and consistent across re-reads.
    """
    table_meta = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if table_meta is None:
        raise resource_not_found(ResourceRef("table", project_id, dataset_id, table_id))

    # Clamp the pagination integers so they can never smuggle SQL even
    # though FastAPI already enforces int types.
    max_results_safe = max(0, min(int(maxResults), 1_000_000))
    start_index_safe = max(0, int(startIndex))
    # ``pageToken`` overrides ``startIndex`` if both are present — the
    # token carries the resume offset. Real BigQuery's pageTokens are
    # opaque base64-encoded protobufs; we round-trip a numeric string.
    if pageToken is not None:
        try:
            start_index_safe = max(0, int(pageToken))
        except ValueError:
            start_index_safe = 0
    projection = _parse_selected_fields(selectedFields, table_meta)
    target_ref = quoted_table_ref(project_id, dataset_id, table_id)
    arrow_table = ctx.engine.fetch_arrow(
        f"SELECT {projection} FROM {target_ref} LIMIT {max_results_safe} OFFSET {start_index_safe}",
    )

    rows = arrow_table_to_bq_rows(arrow_table)
    total_rows = ctx.engine.execute(
        f"SELECT COUNT(*) FROM {target_ref}",
    ).fetchone()
    total = total_rows[0] if total_rows else 0

    response: dict[str, Any] = {
        "kind": "bigquery#tableDataList",
        "totalRows": str(total),
        "rows": rows,
    }
    # Surface a continuation token when more rows remain past this page.
    next_index = start_index_safe + len(rows)
    if next_index < total:
        response["pageToken"] = str(next_index)
    return response


def _parse_selected_fields(selected_fields: str | None, table_meta: Any) -> str:  # noqa: ARG001
    """Translate ``selectedFields=a,b,c`` into a quoted SELECT projection.

    Real BigQuery's ``selectedFields`` is a comma-separated column list
    that projects a subset of the table's columns. Unknown column names
    surface as ``400 invalid`` errors against the live service; the
    emulator passes the request through to DuckDB which raises
    ``Catalog Error`` — the existing error mapper translates that to
    the BQ wire shape. Returns ``"*"`` when no projection is requested.
    """
    if not selected_fields:
        return "*"
    # Split + strip, drop empty entries from trailing commas.
    columns = [c.strip() for c in selected_fields.split(",") if c.strip()]
    if not columns:
        return "*"
    # Quote each column with double-quotes (DuckDB identifier
    # quoting) so reserved-word column names survive the round-trip.
    quoted = [f'"{c}"' for c in columns]
    return ", ".join(quoted)
