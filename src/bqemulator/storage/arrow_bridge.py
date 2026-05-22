"""Arrow ↔ BigQuery REST JSON row format bridge.

BigQuery REST responses represent rows as::

    {"f": [{"v": "value1"}, {"v": "value2"}]}

where every value is a string (even numbers), nulls are ``{"v": null}``,
nested STRUCTs are ``{"v": {"f": [...]}}``, and ARRAYs are
``{"v": [{"v": "elem1"}, ...]}``.

This module converts ``pyarrow.Table`` slices to that shape (for query
result responses) and JSON row payloads back to Arrow (for
``tabledata.insertAll`` ingestion).

Reference:
https://cloud.google.com/bigquery/docs/reference/rest/v2/tabledata/list#response-body
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pyarrow as pa

from bqemulator.types.geography import wkb_to_wkt
from bqemulator.types.interval import format_bq_interval
from bqemulator.types.range_type import detect_range_element

# DuckDB tags GEOMETRY columns with this Arrow extension name when
# emitting via ``to_arrow_table``. We use the metadata to detect and
# WKT-format such columns for BigQuery REST output.
_GEO_EXTENSION_NAME = "geoarrow.wkb"

# UNIX epoch with UTC tz; used as the reference point for integer
# microsecond arithmetic when serialising TIMESTAMP cells. Computing the
# microseconds-since-epoch via ``ts.timestamp() * 1e6`` introduces float
# precision drift at the BigQuery TIMESTAMP boundary (year 9999) — the
# integer ``timedelta`` form avoids it.
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Arrow → BigQuery REST JSON
# ---------------------------------------------------------------------------


def arrow_table_to_bq_rows(
    table: pa.Table,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Convert an Arrow table slice to BigQuery REST row format.

    Args:
        table: The source Arrow table.
        offset: Row offset (0-based).
        limit: Max rows to return. ``None`` means all remaining rows.

    Returns:
        A list of dicts, each ``{"f": [{"v": ...}, ...]}``.
    """
    # PyArrow quirk: ``pa.table({}).slice(0, N)`` returns a sliced table
    # whose ``num_rows`` equals ``N`` even though the source had zero
    # rows — because the column count is also zero, the slice can't be
    # bounded by an actual column length. For DDL responses (CREATE
    # FUNCTION, CREATE SNAPSHOT TABLE, …) where the result is empty,
    # ``getQueryResults`` would otherwise emit ``N`` rows of empty
    # cells, which is exactly the shape that crashes ``bq query``'s
    # table-formatter ("max() iterable argument is empty"). Short-
    # circuit before slicing so the wire response is the correct
    # ``rows: []``.
    if table.num_rows == 0 or table.num_columns == 0:
        return []
    if limit is not None:
        sliced = table.slice(offset, limit)
    elif offset > 0:
        sliced = table.slice(offset)
    else:
        sliced = table

    rows: list[dict[str, Any]] = []
    # Convert to Python dicts column-wise, then reassemble row-wise.
    columns: list[list[Any]] = []
    for i in range(sliced.num_columns):
        col_field = sliced.schema.field(i)
        py_values = sliced.column(i).to_pylist()
        columns.append(
            [_format_bq_value(v, col_field.type, field=col_field) for v in py_values],
        )

    for row_idx in range(sliced.num_rows):
        cells = [{"v": columns[col_idx][row_idx]} for col_idx in range(sliced.num_columns)]
        rows.append({"f": cells})

    return rows


def _bq_range_metadata(field: pa.Field | None) -> tuple[str, bool] | None:
    """Read RANGE metadata off *field*, if any.

    Returns ``(bq_element_type, is_repeated)`` from
    :func:`bqemulator.types.range_type.detect_range_element` when the
    field's DuckDB-side type matches the canonical RANGE-as-STRUCT
    shape. Returns ``None`` otherwise so callers fall through to the
    normal Arrow → BigQuery formatting path.
    """
    if field is None:
        return None
    metadata = field.metadata or {}
    raw = metadata.get(b"bqemu.duckdb_type")
    if raw is None:
        return None
    duckdb_type = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return detect_range_element(duckdb_type)


def _format_range_endpoint(value: Any, bq_elem: str) -> str:
    """Format a single RANGE endpoint for the wire ``"[start, end)"`` string.

    ``None`` becomes the literal token ``UNBOUNDED``. DATE values
    emit ISO ``YYYY-MM-DD``; DATETIME values emit
    ``YYYY-MM-DDTHH:MM:SS[.ffffff]`` (the BigQuery Python client's
    ``datetime_to_py`` parses both forms via ``strptime``). TIMESTAMP
    endpoints emit microseconds-since-epoch as an integer string —
    BigQuery's TIMESTAMP wire-format inside a RANGE is the integer
    form the Python client's ``timestamp_to_py`` parses via ``int()``.
    """
    if value is None:
        return "UNBOUNDED"
    if isinstance(value, datetime):
        if bq_elem == "TIMESTAMP":
            ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            return str(int(ts.timestamp() * 1_000_000))
        # DATETIME — naive ISO 8601 with ``T`` separator so the
        # Python client's ``_RFC3339_*`` formats match. Use
        # ``isoformat()`` (pure-Python; always zero-pads the year)
        # instead of ``strftime("%Y-...")``, which delegates to
        # the platform C library and does NOT zero-pad year < 1000
        # on Linux glibc. The DATETIME boundary cases — year 1 and
        # year 9999 — must round-trip cleanly via the official
        # Python BigQuery client's ``strptime`` parser, which
        # demands a 4-digit ``%Y``.
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _format_range_value(value: Any, bq_elem: str) -> str | None:
    """Encode a Python dict ``{start, end}`` as BigQuery's ``[..., ...)`` string."""
    if value is None:
        return None
    start = value.get("start") if isinstance(value, dict) else None
    end = value.get("end") if isinstance(value, dict) else None
    return f"[{_format_range_endpoint(start, bq_elem)}, {_format_range_endpoint(end, bq_elem)})"


def _format_bq_value(
    value: Any,
    arrow_type: pa.DataType,
    *,
    field: pa.Field | None = None,
) -> Any:
    """Format a single Python value to BigQuery REST wire format.

    Rules (matching the real BigQuery service):
    - ``None`` → ``None`` (rendered as JSON ``null``).
    - INT64 / FLOAT64 → string (``"123"``, ``"1.5"``).
    - NUMERIC / BIGNUMERIC → string with full precision.
    - BOOL → string ``"true"`` / ``"false"``.
    - STRING → string as-is.
    - BYTES → base64-encoded string.
    - GEOGRAPHY (Arrow ``geoarrow.wkb`` extension) → WKT string.
    - INTERVAL (Arrow ``month_day_nano_interval``) → BigQuery-canonical
      ``"Y-M D H:M:S[.ffffff]"`` form.
    - DATE → ``"YYYY-MM-DD"``.
    - TIME → ``"HH:MM:SS.ffffff"``.
    - DATETIME (Arrow TIMESTAMP without tz) → ``"YYYY-MM-DDTHH:MM:SS.ffffff"``.
    - TIMESTAMP (Arrow TIMESTAMP with tz) → ``"YYYY-MM-DD HH:MM:SS.ffffff UTC"``.
    - JSON → string.
    - ARRAY<T> → list of ``{"v": ...}`` dicts (recursive). A NULL
      ARRAY renders as an empty list: BigQuery REPEATED columns are
      never NULL, only empty, and the ``google-cloud-bigquery`` row
      parser iterates the value unconditionally.
    - STRUCT<…> → ``{"f": [{"v": ...}, ...]}`` (recursive).
    """
    range_meta = _bq_range_metadata(field)

    if value is None:
        if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
            return []
        return None

    # ADR 0023 §1.G — BigQuery RANGE columns surface on the wire as a
    # single string (``[start, end)``). For a REPEATED RANGE the
    # element string is wrapped in the usual ``{"v": ...}`` form.
    if range_meta is not None:
        bq_elem, is_repeated = range_meta
        if is_repeated:
            if isinstance(value, list):
                return [{"v": _format_range_value(elem, bq_elem)} for elem in value]
            return []
        return _format_range_value(value, bq_elem)

    # GEOGRAPHY — encoded as Arrow binary with the geoarrow.wkb extension.
    if field is not None and _is_geometry_field(field) and isinstance(value, (bytes, bytearray)):
        return wkb_to_wkt(value)

    # INTERVAL — Arrow month_day_nano interval.
    if pa.types.is_interval(arrow_type):
        # pyarrow returns a MonthDayNano namedtuple-like object.
        months = getattr(value, "months", 0)
        days = getattr(value, "days", 0)
        nanos = getattr(value, "nanoseconds", 0)
        return format_bq_interval(int(months), int(days), int(nanos))

    # Integers
    if pa.types.is_integer(arrow_type):
        return str(value)

    # Floats
    if pa.types.is_floating(arrow_type):
        return str(value)

    # Decimals (NUMERIC / BIGNUMERIC)
    if pa.types.is_decimal(arrow_type):
        if isinstance(value, Decimal):
            return str(value)
        return str(value)

    # Boolean
    if pa.types.is_boolean(arrow_type):
        return "true" if value else "false"

    # String
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return str(value)

    # Binary / Bytes (non-GEOGRAPHY).
    if pa.types.is_binary(arrow_type) or pa.types.is_large_binary(arrow_type):
        import base64

        if isinstance(value, (bytes, bytearray)):
            return base64.b64encode(value).decode("ascii")
        return str(value)

    # Date
    if pa.types.is_date(arrow_type):
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    # Time
    if pa.types.is_time(arrow_type):
        if isinstance(value, time):
            return value.isoformat()
        return str(value)

    # Timestamp (with or without timezone)
    if pa.types.is_timestamp(arrow_type):
        if isinstance(value, datetime):
            if arrow_type.tz is not None:
                # BigQuery TIMESTAMP wire format: microseconds-since-epoch
                # as an integer string. The official Python client's
                # ``timestamp_to_py`` decodes it via ``_EPOCH +
                # timedelta(microseconds=int(value))`` which supports the
                # full BigQuery TIMESTAMP range (0001-01-01 through
                # 9999-12-31). We compute the delta in integer
                # microseconds to avoid the float-precision drift the
                # ``ts.timestamp() * 1_000_000`` form exhibits at the
                # 9999 boundary.
                ts = value
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                delta = ts - _UNIX_EPOCH
                micros = (
                    delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
                )
                return str(micros)
            # BigQuery DATETIME format: ``YYYY-MM-DDTHH:MM:SS[.ffffff]``.
            # Use ``isoformat()`` (always zero-pads the year) over
            # ``strftime("%Y-...")`` so the year-1 / year-9999
            # boundary cases parse correctly through the Python
            # client's ``strptime`` chain. See the matching note in
            # ``_format_range_endpoint`` above.
            return value.isoformat()
        return str(value)

    # List / Array
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        element_type = arrow_type.value_type
        element_field = arrow_type.value_field if hasattr(arrow_type, "value_field") else None
        if isinstance(value, list):
            return [
                {"v": _format_bq_value(elem, element_type, field=element_field)} for elem in value
            ]
        return str(value)

    # Struct
    if pa.types.is_struct(arrow_type):
        if isinstance(value, dict):
            fields = []
            for i in range(arrow_type.num_fields):
                child_field = arrow_type.field(i)
                field_value = value.get(child_field.name)
                fields.append(
                    {"v": _format_bq_value(field_value, child_field.type, field=child_field)},
                )
            return {"f": fields}
        return str(value)

    # Fallback for unhandled types (JSON etc. — as string).
    return str(value)


def _is_geometry_field(field: pa.Field) -> bool:
    """Detect a DuckDB GEOMETRY column via its Arrow extension metadata."""
    md = field.metadata or {}
    # Arrow metadata keys/values come back as bytes.
    for key, value in md.items():
        if key == b"ARROW:extension:name" and value == _GEO_EXTENSION_NAME.encode():
            return True
    return False


# ---------------------------------------------------------------------------
# BigQuery REST JSON → Arrow (for insertAll)
# ---------------------------------------------------------------------------


def bq_rows_to_arrow(
    rows: list[dict[str, Any]],
    schema: pa.Schema,
) -> pa.Table:
    """Convert BigQuery REST row format into an Arrow table.

    Args:
        rows: List of ``{"json": {"col1": val1, "col2": val2, ...}}``
              dicts (as received in ``tabledata.insertAll`` requests).
        schema: The target Arrow schema.

    Returns:
        A ``pyarrow.Table`` with the given schema.
    """
    if not rows:
        return pa.table({field.name: pa.array([], type=field.type) for field in schema})

    column_data: dict[str, list[Any]] = {field.name: [] for field in schema}
    for row in rows:
        json_data = row.get("json", row)
        for field in schema:
            raw = json_data.get(field.name)
            column_data[field.name].append(
                _coerce_to_arrow_value(raw, field.type, field=field),
            )

    arrays = {field.name: pa.array(column_data[field.name], type=field.type) for field in schema}
    return pa.table(arrays, schema=schema)


def _coerce_to_arrow_value(
    value: Any,
    arrow_type: pa.DataType,
    *,
    field: pa.Field | None = None,
) -> Any:
    """Coerce a JSON-deserialized Python value to a pyarrow-compatible value.

    BigQuery REST sends everything as strings (or nested dicts/lists).
    This function converts ``"123"`` → ``123`` for INT64, etc.

    If *field* is provided and carries a ``bq_type`` metadata entry the
    coercion can dispatch on the BigQuery type name in addition to the
    Arrow type — used for GEOGRAPHY (WKT string → WKB hex string).
    """
    if value is None:
        return None

    bq_type = _bq_type_metadata(field)

    # GEOGRAPHY — accept WKT strings and convert to hex-encoded WKB so
    # the downstream INSERT can ``ST_GeomFromHEXWKB`` them.
    if bq_type == "GEOGRAPHY":
        return _wkt_to_wkb_hex(str(value))

    # INTERVAL — accept BigQuery canonical strings or pre-built tuples.
    if pa.types.is_interval(arrow_type) or bq_type == "INTERVAL":
        return _coerce_interval(value)

    # Integers
    if pa.types.is_integer(arrow_type):
        return int(value)

    # Floats
    if pa.types.is_floating(arrow_type):
        return float(value)

    # Decimals
    if pa.types.is_decimal(arrow_type):
        return Decimal(str(value))

    # Boolean
    if pa.types.is_boolean(arrow_type):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    # String
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return str(value)

    # Binary
    if pa.types.is_binary(arrow_type) or pa.types.is_large_binary(arrow_type):
        import base64

        if isinstance(value, str):
            return base64.b64decode(value)
        return bytes(value)

    # List / Array
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        element_field = arrow_type.value_field if hasattr(arrow_type, "value_field") else None
        if isinstance(value, list):
            return [
                _coerce_to_arrow_value(v, arrow_type.value_type, field=element_field) for v in value
            ]
        return value

    # Struct
    if pa.types.is_struct(arrow_type):
        if isinstance(value, dict):
            return {
                arrow_type.field(i).name: _coerce_to_arrow_value(
                    value.get(arrow_type.field(i).name),
                    arrow_type.field(i).type,
                    field=arrow_type.field(i),
                )
                for i in range(arrow_type.num_fields)
            }
        return value

    # Timestamp
    if pa.types.is_timestamp(arrow_type):
        if isinstance(value, str):
            from datetime import datetime as dt

            # Parse ISO-8601 strings. BigQuery sends both "2026-04-15T00:00:00Z"
            # and "2026-04-15 00:00:00 UTC" formats.
            cleaned = value.replace(" UTC", "+00:00").replace("Z", "+00:00")
            try:
                return dt.fromisoformat(cleaned)
            except ValueError:
                return value
        return value

    # Date
    if pa.types.is_date(arrow_type):
        if isinstance(value, str):
            from datetime import date as d

            try:
                return d.fromisoformat(value)
            except ValueError:
                return value
        return value

    # Time
    if pa.types.is_time(arrow_type):
        if isinstance(value, str):
            from datetime import time as tm

            try:
                return tm.fromisoformat(value)
            except ValueError:
                return value
        return value

    # Fallback (JSON, unknown types) — return as-is and let pyarrow
    # handle it or raise a clean error.
    return value


# ---------------------------------------------------------------------------
# Phase 9 specialized-type coercion helpers.
# ---------------------------------------------------------------------------


def _bq_type_metadata(field: pa.Field | None) -> str | None:
    """Read the ``bq_type`` metadata entry from an Arrow field, if present.

    The schema builder in ``api.routes.tabledata`` stamps each Arrow
    field with the originating BigQuery type so this module can
    dispatch on GEOGRAPHY / RANGE / INTERVAL semantics that the Arrow
    type alone doesn't capture (e.g. GEOGRAPHY backs onto pa.string()).
    """
    if field is None:
        return None
    md = field.metadata or {}
    raw = md.get(b"bq_type")
    if raw is None:
        return None
    return raw.decode("ascii") if isinstance(raw, (bytes, bytearray)) else str(raw)


def _wkt_to_wkb_hex(wkt: str) -> str:
    """Convert a WKT string to upper-case hex-encoded WKB.

    Used by the insertAll path: BigQuery sends ``"POINT(1 2)"`` as a
    GEOGRAPHY value; the emulator stores it as a DuckDB ``GEOMETRY``.
    The downstream INSERT applies ``ST_GeomFromHEXWKB`` to turn the
    hex back into a geometry, and the round-trip is faithful.
    """
    from bqemulator.types.geography import _ensure_conv_conn

    conn = _ensure_conv_conn()
    row = conn.execute(
        "SELECT lower(hex(ST_AsWKB(ST_GeomFromText(?))))",
        [wkt],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"Cannot parse WKT: {wkt!r}")
    return str(row[0]).upper()


def _coerce_interval(value: Any) -> Any:
    """Coerce a JSON value into an Arrow MonthDayNano-compatible tuple.

    Accepts:
    * ``"1-2 3 4:5:6.789"`` — BigQuery canonical string form.
    * ``"1-2"`` / ``"3"`` (single-component shorthands — interpreted as
      ``YEAR TO MONTH`` / ``DAY``).
    * Any other string Arrow accepts directly.
    * Already-parsed ``MonthDayNano`` / 3-tuples.
    """
    if isinstance(value, tuple) and len(value) == 3:  # noqa: PLR2004
        return value
    if hasattr(value, "months") and hasattr(value, "days") and hasattr(value, "nanoseconds"):
        return (int(value.months), int(value.days), int(value.nanoseconds))
    if isinstance(value, str):
        return _bq_interval_string_to_tuple(value)
    return value


def _bq_interval_string_to_tuple(text: str) -> tuple[int, int, int]:
    """Parse a BigQuery interval canonical string into ``(months, days, nanos)``."""
    from bqemulator.types.interval import (
        parse_interval_literal,
    )

    cleaned = text.strip()
    # Best-effort detection: ``Y-M D H:M:S`` → YEAR TO SECOND form.
    if " " in cleaned and ":" in cleaned and "-" in cleaned.split()[0]:
        parts = parse_interval_literal(cleaned, "YEAR TO SECOND")
    elif " " in cleaned and ":" in cleaned:
        # ``D H:M:S`` → DAY TO SECOND.
        parts = parse_interval_literal(cleaned, "DAY TO SECOND")
    elif "-" in cleaned and " " not in cleaned and ":" not in cleaned:
        parts = parse_interval_literal(cleaned, "YEAR TO MONTH")
    elif ":" in cleaned and " " not in cleaned and "-" not in cleaned:
        # ``H:M:S`` or ``H:M`` → HOUR TO SECOND / HOUR TO MINUTE.
        if cleaned.count(":") == 2:  # noqa: PLR2004
            parts = parse_interval_literal(cleaned, "HOUR TO SECOND")
        else:
            parts = parse_interval_literal(cleaned, "HOUR TO MINUTE")
    else:
        # Fall back to single-day shorthand.
        parts = parse_interval_literal(cleaned, "DAY")
    months = parts.years * 12 + parts.months
    nanos = (
        parts.hours * 3600 * 1_000_000_000
        + parts.minutes * 60 * 1_000_000_000
        + int(parts.seconds * 1_000_000_000)
    )
    return (months, parts.days, nanos)


def arrow_type_to_bq_type_name(arrow_type: Any) -> str:
    """Map an Arrow scalar type to a BigQuery type name.

    Used by the catalog-sync helpers and the materialized-view manager
    when introspecting a freshly-created DuckDB table to build a
    :class:`bqemulator.catalog.models.TableSchema`. Both call sites
    only need scalar leaf-type names — REPEATED / record handling is
    not part of this helper.
    """
    if pa.types.is_int64(arrow_type) or pa.types.is_int32(arrow_type):
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
    if pa.types.is_decimal(arrow_type):
        return "NUMERIC"
    if pa.types.is_binary(arrow_type):
        return "BYTES"
    return "STRING"


def introspect_arrow_schema(engine: Any, target_ref: str) -> Any:
    """Return the Arrow schema of a DuckDB table referenced by ``target_ref``.

    ``target_ref`` is the already-quoted ``"schema"."table"`` form
    produced by :func:`bqemulator.storage.sql_identifiers.quoted_table_ref`.
    """
    result = engine.execute(f"SELECT * FROM {target_ref} LIMIT 0")
    if hasattr(result, "to_arrow_table"):
        return result.to_arrow_table().schema
    return result.fetch_arrow_table().schema


__all__ = [
    "arrow_table_to_bq_rows",
    "arrow_type_to_bq_type_name",
    "bq_rows_to_arrow",
    "introspect_arrow_schema",
]
