"""Type mapping for routine arguments and return types.

BigQuery routine argument / return types arrive as
``StandardSqlDataType`` dicts in the REST protocol::

    {"typeKind": "INT64"}
    {"typeKind": "ARRAY", "arrayElementType": {"typeKind": "STRING"}}
    {"typeKind": "STRUCT", "structType": {"fields": [...]}}

This module renders them as DuckDB type strings for ``CREATE MACRO``
signatures and maps them to pyarrow types for JS UDF conversion.

The mapping is narrower than :mod:`bqemulator.storage.type_map` because
routines only support a subset of BigQuery's type system.
"""

from __future__ import annotations

from typing import Any

from bqemulator.domain.errors import InvalidQueryError

# BigQuery StandardSqlDataType typeKind → DuckDB type string.
_SCALAR_MAP: dict[str, str] = {
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "INT64": "BIGINT",
    "INTEGER": "BIGINT",
    "FLOAT64": "DOUBLE",
    "FLOAT": "DOUBLE",
    "NUMERIC": "DECIMAL(38,9)",
    "BIGNUMERIC": "DECIMAL(38,9)",
    "STRING": "VARCHAR",
    "BYTES": "BLOB",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP WITH TIME ZONE",
    "JSON": "JSON",
    "GEOGRAPHY": "VARCHAR",
    "INTERVAL": "INTERVAL",
}


def render_duckdb_type(bq_type: dict[str, Any] | None) -> str:
    """Render a BigQuery StandardSqlDataType to a DuckDB type string.

    Args:
        bq_type: ``{"typeKind": "...", ...}`` or ``None`` for ANY_TYPE.

    Returns:
        The DuckDB type string (e.g. ``BIGINT``, ``ARRAY(VARCHAR)``).

    Raises:
        InvalidQueryError: If the type is unrecognised.
    """
    if bq_type is None:
        return "ANY"
    kind = str(bq_type.get("typeKind", "")).upper()
    if not kind:
        raise InvalidQueryError("Routine type has no typeKind")

    if kind in _SCALAR_MAP:
        return _SCALAR_MAP[kind]

    if kind == "ARRAY":
        element = bq_type.get("arrayElementType")
        if element is None:
            raise InvalidQueryError("ARRAY type missing arrayElementType")
        return f"{render_duckdb_type(element)}[]"

    if kind == "STRUCT":
        struct = bq_type.get("structType", {})
        fields = struct.get("fields", [])
        if not fields:
            raise InvalidQueryError("STRUCT type missing fields")
        rendered = []
        for field in fields:
            name = field.get("name")
            if not name:
                raise InvalidQueryError("STRUCT field missing name")
            rendered.append(f'"{name}" {render_duckdb_type(field.get("type"))}')
        return f"STRUCT({', '.join(rendered)})"

    raise InvalidQueryError(f"Unsupported routine type: {kind}")


def parse_bq_type_string(text: str) -> dict[str, Any]:
    """Parse a BigQuery type string (``ARRAY<INT64>``, ``STRUCT<a INT64, b STRING>``).

    BigQuery's REST API surfaces routine argument / return types as
    nested ``StandardSqlDataType`` dicts (e.g.
    ``{"typeKind": "ARRAY", "arrayElementType": {"typeKind": "INT64"}}``).
    The script parser captures the source text instead — so we need this
    helper to normalise the captured string into the dict shape every
    downstream consumer (``render_duckdb_type``, the JS UDF runtime's
    type-aware coercion, INFORMATION_SCHEMA, etc.) already expects.

    Trailing ``(precision[, scale])`` on scalar types is stripped because
    DuckDB applies its own precision rules; the precision is preserved
    only for the ``NUMERIC`` / ``BIGNUMERIC`` mappings in
    :data:`_SCALAR_MAP`.
    """
    stripped = text.strip()
    upper = stripped.upper()

    if upper.startswith("ARRAY<") and stripped.endswith(">"):
        inner = stripped[len("ARRAY<") : -1].strip()
        return {"typeKind": "ARRAY", "arrayElementType": parse_bq_type_string(inner)}

    if upper.startswith("STRUCT<") and stripped.endswith(">"):
        body = stripped[len("STRUCT<") : -1].strip()
        fields = []
        for raw_field_str in _split_struct_fields(body):
            field_str = raw_field_str.strip()
            if not field_str:
                continue
            sep = _find_first_top_level_whitespace(field_str)
            if sep <= 0:
                raise InvalidQueryError(f"Malformed STRUCT field: {field_str!r}")
            name = field_str[:sep].strip().strip("`")
            type_part = field_str[sep:].strip()
            fields.append({"name": name, "type": parse_bq_type_string(type_part)})
        return {"typeKind": "STRUCT", "structType": {"fields": fields}}

    paren = stripped.find("(")
    if paren >= 0:
        return {"typeKind": stripped[:paren].strip().upper()}
    return {"typeKind": upper}


def _split_struct_fields(body: str) -> list[str]:
    """Split a STRUCT body on top-level commas, respecting nested ``<>``/``()``."""
    out: list[str] = []
    depth_angle = 0
    depth_paren = 0
    buf: list[str] = []
    for ch in body:
        if ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
        elif ch == "," and depth_angle == 0 and depth_paren == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _find_first_top_level_whitespace(text: str) -> int:
    """Find the first whitespace index outside any nested ``<>``/``()``."""
    depth_angle = 0
    depth_paren = 0
    for i, ch in enumerate(text):
        if ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
        elif ch.isspace() and depth_angle == 0 and depth_paren == 0:
            return i
    return -1


def python_to_json_coerce(*, value: object) -> object:
    """Prepare a Python value for JSON transport into a JS UDF.

    BigQuery's JS UDF calling convention serialises arguments to JSON.
    ``bytes`` goes as base64 string; ``Decimal`` as string; ``datetime``
    as ISO string. Everything else falls through to orjson's default.
    """
    import base64
    from datetime import date, datetime, time
    from decimal import Decimal

    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


__all__ = ["parse_bq_type_string", "python_to_json_coerce", "render_duckdb_type"]
