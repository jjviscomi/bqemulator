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

from collections.abc import Callable
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


def _fmt_bq_binary(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format a BYTES value as base64 per the BigQuery REST contract."""
    import base64

    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _fmt_bq_date(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format a DATE value as ``YYYY-MM-DD``."""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _fmt_bq_time(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format a TIME value as ``HH:MM:SS[.ffffff]``."""
    if isinstance(value, time):
        return value.isoformat()
    return str(value)


def _fmt_bq_timestamp(value: Any, arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format a TIMESTAMP / DATETIME Arrow value for the BigQuery REST wire.

    - Arrow TIMESTAMP with a ``tz`` is BigQuery TIMESTAMP — emitted as
      a microseconds-since-epoch integer string. We compute the delta
      in integer microseconds (not ``timestamp() * 1_000_000``) to
      avoid float-precision drift at the year-9999 boundary; the
      official Python client decodes via ``_EPOCH + timedelta(microseconds=int(value))``.
    - Arrow TIMESTAMP without a ``tz`` is BigQuery DATETIME — emitted
      via ``isoformat()`` so year-1 / year-9999 boundaries parse
      correctly through the Python client's ``strptime`` chain (and
      ``isoformat`` always zero-pads ``%Y`` to four digits).
    """
    if not isinstance(value, datetime):
        return str(value)
    if arrow_type.tz is not None:
        ts = value
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta = ts - _UNIX_EPOCH
        micros = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        return str(micros)
    return value.isoformat()


def _fmt_bq_list(value: Any, arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format an ARRAY value as ``[{"v": ...}, ...]`` (recursive)."""
    if not isinstance(value, list):
        return str(value)
    element_type = arrow_type.value_type
    element_field = arrow_type.value_field if hasattr(arrow_type, "value_field") else None
    return [{"v": _format_bq_value(elem, element_type, field=element_field)} for elem in value]


def _fmt_bq_struct(value: Any, arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Format a STRUCT value as ``{"f": [{"v": ...}, ...]}`` (recursive)."""
    if not isinstance(value, dict):
        return str(value)
    fields = []
    for i in range(arrow_type.num_fields):
        child_field = arrow_type.field(i)
        field_value = value.get(child_field.name)
        fields.append(
            {"v": _format_bq_value(field_value, child_field.type, field=child_field)},
        )
    return {"f": fields}


# Predicate → formatter dispatch for ``_format_bq_value``. Order matters
# in principle but in practice the Arrow type predicates are mutually
# exclusive (a type is never both a list and a struct, etc.); the
# ordering below mirrors the original procedural chain for parity with
# the pre-refactor reading order.
_BQ_TYPE_FORMATTERS: tuple[tuple[Callable[[pa.DataType], bool], Callable[..., Any]], ...] = (
    (pa.types.is_integer, lambda v, _t, _f: str(v)),
    (pa.types.is_floating, lambda v, _t, _f: str(v)),
    (pa.types.is_decimal, lambda v, _t, _f: str(v)),
    (pa.types.is_boolean, lambda v, _t, _f: "true" if v else "false"),
    (lambda t: pa.types.is_string(t) or pa.types.is_large_string(t), lambda v, _t, _f: str(v)),
    (lambda t: pa.types.is_binary(t) or pa.types.is_large_binary(t), _fmt_bq_binary),
    (pa.types.is_date, _fmt_bq_date),
    (pa.types.is_time, _fmt_bq_time),
    (pa.types.is_timestamp, _fmt_bq_timestamp),
    (lambda t: pa.types.is_list(t) or pa.types.is_large_list(t), _fmt_bq_list),
    (pa.types.is_struct, _fmt_bq_struct),
)


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
    # Pre-checks driven by ``field`` metadata or by Arrow-internal
    # types whose handlers don't fit the regular dispatch shape (they
    # consult the BigQuery RANGE / GEOGRAPHY / INTERVAL metadata, not
    # the Arrow column type alone).
    if value is None:
        if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
            return []
        return None

    # ADR 0023 §1.G — BigQuery RANGE columns surface on the wire as a
    # single string (``[start, end)``). For a REPEATED RANGE the
    # element string is wrapped in the usual ``{"v": ...}`` form.
    range_meta = _bq_range_metadata(field)
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

    # INTERVAL — Arrow month_day_nano interval (a struct in pyarrow's
    # internal representation, so the dispatch table's struct
    # predicate would otherwise capture it).
    if pa.types.is_interval(arrow_type):
        months = getattr(value, "months", 0)
        days = getattr(value, "days", 0)
        nanos = getattr(value, "nanoseconds", 0)
        return format_bq_interval(int(months), int(days), int(nanos))

    # Regular Arrow-type dispatch.
    for predicate, formatter in _BQ_TYPE_FORMATTERS:
        if predicate(arrow_type):
            return formatter(value, arrow_type, field)

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


def _coerce_arrow_boolean(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Coerce a BigQuery REST value to a Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _coerce_arrow_binary(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Coerce a BigQuery BYTES value to Python ``bytes``.

    Accepts two well-defined shapes:

    * ``str`` — base64-encoded, the REST/insertAll wire form. Decoded
      with ``validate=True`` so malformed base64 raises
      ``binascii.Error`` (a ``ValueError`` subclass) rather than
      silently producing partial bytes. Mirrors real BigQuery's
      ``400 invalid: Could not decode bytes`` on bad payloads.
    * ``bytes`` / ``bytearray`` — passed through unchanged. The Storage
      Write proto path (``streaming/proto_deserializer.py``) feeds
      proto-decoded BYTES fields here as raw ``bytes``; that flow has
      already validated the wire encoding upstream and a strict
      base64 check would mis-fire on the binary payload.

    Any other type raises ``TypeError``. Tolerating arbitrary iterables
    of ints would mask real caller bugs; strictness here matches the
    BigQuery contract and surfaces those errors loudly.
    """
    import base64

    if isinstance(value, str):
        return base64.b64decode(value, validate=True)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise TypeError(
        f"BYTES value must be a base64 str or bytes/bytearray, got {type(value).__name__}",
    )


def _coerce_arrow_list(value: Any, arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Coerce a BigQuery REST ARRAY into a Python ``list`` of coerced elements."""
    if not isinstance(value, list):
        return value
    element_field = arrow_type.value_field if hasattr(arrow_type, "value_field") else None
    return [_coerce_to_arrow_value(v, arrow_type.value_type, field=element_field) for v in value]


def _coerce_arrow_struct(value: Any, arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Coerce a BigQuery REST STRUCT into a Python ``dict`` of coerced fields."""
    if not isinstance(value, dict):
        return value
    return {
        arrow_type.field(i).name: _coerce_to_arrow_value(
            value.get(arrow_type.field(i).name),
            arrow_type.field(i).type,
            field=arrow_type.field(i),
        )
        for i in range(arrow_type.num_fields)
    }


def _coerce_arrow_timestamp(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Parse BigQuery's TIMESTAMP/DATETIME string forms into Python ``datetime``."""
    if not isinstance(value, str):
        return value
    # BigQuery sends both "2026-04-15T00:00:00Z" and "2026-04-15 00:00:00 UTC" forms.
    cleaned = value.replace(" UTC", "+00:00").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return value


def _coerce_arrow_date(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Parse BigQuery's DATE string form into Python ``date``."""
    if not isinstance(value, str):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        return value


def _coerce_arrow_time(value: Any, _arrow_type: pa.DataType, _field: pa.Field | None) -> Any:
    """Parse BigQuery's TIME string form into Python ``time``."""
    if not isinstance(value, str):
        return value
    try:
        return time.fromisoformat(value)
    except ValueError:
        return value


# Predicate → coercer dispatch for ``_coerce_to_arrow_value``. The
# ordering mirrors the original procedural chain — Arrow type
# predicates are mutually exclusive so order is documentation-only,
# but readers expect to find INT64 / FLOAT64 first.
_ARROW_TYPE_COERCERS: tuple[tuple[Callable[[pa.DataType], bool], Callable[..., Any]], ...] = (
    (pa.types.is_integer, lambda v, _t, _f: int(v)),
    (pa.types.is_floating, lambda v, _t, _f: float(v)),
    (pa.types.is_decimal, lambda v, _t, _f: Decimal(str(v))),
    (pa.types.is_boolean, _coerce_arrow_boolean),
    (lambda t: pa.types.is_string(t) or pa.types.is_large_string(t), lambda v, _t, _f: str(v)),
    (lambda t: pa.types.is_binary(t) or pa.types.is_large_binary(t), _coerce_arrow_binary),
    (lambda t: pa.types.is_list(t) or pa.types.is_large_list(t), _coerce_arrow_list),
    (pa.types.is_struct, _coerce_arrow_struct),
    (pa.types.is_timestamp, _coerce_arrow_timestamp),
    (pa.types.is_date, _coerce_arrow_date),
    (pa.types.is_time, _coerce_arrow_time),
)


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
    # ``bq_type == "INTERVAL"`` covers the case where the Arrow column
    # type didn't preserve the interval-ness (e.g. struct-encoded).
    if pa.types.is_interval(arrow_type) or bq_type == "INTERVAL":
        return _coerce_interval(value)

    # Regular Arrow-type dispatch.
    for predicate, coercer in _ARROW_TYPE_COERCERS:
        if predicate(arrow_type):
            return coercer(value, arrow_type, field)

    # Fallback (JSON, unknown types) — return as-is and let pyarrow
    # handle it or raise a clean error.
    return value


# ---------------------------------------------------------------------------
# Specialized-type coercion helpers (GEOGRAPHY, RANGE, INTERVAL).
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
