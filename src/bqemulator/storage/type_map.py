"""Bidirectional BigQuery ↔ DuckDB type mapping.

This module is the single source of truth for how BigQuery types translate
to DuckDB types and back. Every other module (SQL translator, Arrow bridge,
catalog DDL generator) consults this mapping.

The mapping is not 1:1 in every case — see the ``Notes`` column in
:data:`TYPE_MAP` for cases where precision or semantics differ.

Supported BigQuery types
------------------------
INT64, FLOAT64, NUMERIC, BIGNUMERIC, BOOL, STRING, BYTES, DATE, TIME,
DATETIME, TIMESTAMP, JSON, ARRAY<T>, STRUCT<…>, GEOGRAPHY, RANGE<T>,
INTERVAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bqemulator.domain.errors import ValidationError
from bqemulator.types.range_type import duckdb_struct_for, parse_bq_range_type


@dataclass(slots=True, frozen=True)
class TypeMapping:
    """A single mapping between a BigQuery type name and its DuckDB equivalent."""

    bq_type: str
    duckdb_type: str
    notes: str = ""


# Ordered list — looked up by bq_type or duckdb_type via helpers below.
TYPE_MAP: tuple[TypeMapping, ...] = (
    TypeMapping("INT64", "BIGINT"),
    TypeMapping("FLOAT64", "DOUBLE"),
    TypeMapping("NUMERIC", "DECIMAL(38, 9)", notes="fixed precision 38, scale 9"),
    TypeMapping("BIGNUMERIC", "DECIMAL(76, 38)", notes="fixed precision 76, scale 38"),
    TypeMapping("BOOL", "BOOLEAN"),
    TypeMapping("STRING", "VARCHAR"),
    TypeMapping("BYTES", "BLOB"),
    TypeMapping("DATE", "DATE"),
    TypeMapping("TIME", "TIME"),
    TypeMapping("DATETIME", "TIMESTAMP", notes="BigQuery DATETIME is timezone-naive"),
    TypeMapping("TIMESTAMP", "TIMESTAMPTZ", notes="BigQuery TIMESTAMP is always UTC"),
    TypeMapping("JSON", "JSON"),
    # Specialized BigQuery types.
    TypeMapping("GEOGRAPHY", "GEOMETRY", notes="DuckDB spatial extension; planar (not spheroidal)"),
    TypeMapping("INTERVAL", "INTERVAL", notes="Native DuckDB INTERVAL"),
    # ARRAY, STRUCT, and RANGE<T> are parameterized — handled specially below.
)

# Lookup indices.
_BQ_TO_DUCKDB: dict[str, str] = {m.bq_type: m.duckdb_type for m in TYPE_MAP}
# Legacy BigQuery type-name aliases. The official ``bigquery`` Java and
# Go clients still send ``INTEGER``/``FLOAT``/``BOOLEAN``/``RECORD`` on
# the wire by default — real BigQuery accepts those as synonyms for
# ``INT64``/``FLOAT64``/``BOOL``/``STRUCT``. Wire them here so cross-
# language E2E doesn't trip over the legacy names.
_BQ_TO_DUCKDB.update(
    {
        "INTEGER": _BQ_TO_DUCKDB["INT64"],
        "FLOAT": _BQ_TO_DUCKDB["FLOAT64"],
        "BOOLEAN": _BQ_TO_DUCKDB["BOOL"],
    },
)
_DUCKDB_TO_BQ: dict[str, str] = {m.duckdb_type: m.bq_type for m in TYPE_MAP}

# Additional reverse-mapping aliases that DuckDB uses interchangeably.
_DUCKDB_ALIASES: dict[str, str] = {
    "INTEGER": "INT64",
    "INT": "INT64",
    "BIGINT": "INT64",
    "SMALLINT": "INT64",
    "TINYINT": "INT64",
    "HUGEINT": "INT64",
    "FLOAT": "FLOAT64",
    "REAL": "FLOAT64",
    "DOUBLE": "FLOAT64",
    "BOOLEAN": "BOOL",
    "VARCHAR": "STRING",
    "TEXT": "STRING",
    "BLOB": "BYTES",
    "BYTEA": "BYTES",
    "DATE": "DATE",
    "TIME": "TIME",
    "TIMESTAMP": "DATETIME",
    "TIMESTAMPTZ": "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMP",
    "JSON": "JSON",
    # Specialized BigQuery types.
    "GEOMETRY": "GEOGRAPHY",
    "INTERVAL": "INTERVAL",
}

# BigQuery standard-SQL type name → legacy REST ("wire") type name. Real
# BigQuery reports the legacy names in its REST schema (``tables.get`` and
# the load-job schema), and the catalog stores them so every code path
# agrees: the DDL path normalizes via ``ddl_result.DDL_BQ_WIRE_TYPES`` and
# explicit-schema loads arrive already legacy-named. Autodetect inference
# routes through here so it matches. Names absent from this map are spelled
# identically in both forms (STRING, BYTES, NUMERIC, BIGNUMERIC, DATE, TIME,
# DATETIME, TIMESTAMP, JSON, GEOGRAPHY, ...).
_BQ_STANDARD_TO_WIRE: dict[str, str] = {
    "INT64": "INTEGER",
    "FLOAT64": "FLOAT",
    "BOOL": "BOOLEAN",
    "STRUCT": "RECORD",
}


def bq_to_duckdb(bq_type: str) -> str:
    """Convert a BigQuery type name to its DuckDB equivalent.

    Handles parameterized types:
    - ``ARRAY<INT64>`` → ``BIGINT[]``
    - ``STRUCT<name STRING, age INT64>`` → ``STRUCT(name VARCHAR, age BIGINT)``
    - ``RANGE<DATE>`` → ``STRUCT("start" DATE, "end" DATE)``

    Raises :class:`ValidationError` for unknown types.
    """
    upper = bq_type.strip().upper()

    # ARRAY<element_type>
    if upper.startswith("ARRAY<") and upper.endswith(">"):
        inner = bq_type.strip()[6:-1].strip()
        return f"{bq_to_duckdb(inner)}[]"

    # STRUCT<field1 type1, field2 type2, ...>
    if upper.startswith("STRUCT<") and upper.endswith(">"):
        inner = bq_type.strip()[7:-1].strip()
        fields = _parse_struct_fields(inner)
        duckdb_fields = ", ".join(f"{name} {bq_to_duckdb(ft)}" for name, ft in fields)
        return f"STRUCT({duckdb_fields})"

    # RANGE<element_type>
    if upper.startswith("RANGE<") and upper.endswith(">"):
        element = parse_bq_range_type(bq_type)
        return duckdb_struct_for(element)

    result = _BQ_TO_DUCKDB.get(upper)
    if result is None:
        raise ValidationError(f"Unknown BigQuery type: {bq_type!r}")
    return result


def _duckdb_compound_to_bq(duckdb_type: str, upper: str) -> str | None:
    """Map a parameterized DuckDB type (ARRAY/LIST/STRUCT) to BigQuery, else ``None``.

    Recurses into :func:`duckdb_to_bq` for element / field types. Returns
    ``None`` for non-compound types so the caller falls through to the
    DECIMAL handling and the direct / alias lookups.
    """
    stripped = duckdb_type.strip()
    if upper.endswith("[]"):  # ARRAY suffix, e.g. ``BIGINT[]``
        return f"ARRAY<{duckdb_to_bq(stripped[:-2].strip())}>"
    if upper.startswith(("LIST(", "LIST (")):
        return f"ARRAY<{duckdb_to_bq(_extract_parens(stripped, 'LIST'))}>"
    if upper.startswith(("STRUCT(", "STRUCT (")):
        fields = _parse_struct_fields(_extract_parens(stripped, "STRUCT"))
        bq_fields = ", ".join(f"{name} {duckdb_to_bq(ft)}" for name, ft in fields)
        return f"STRUCT<{bq_fields}>"
    return None


def duckdb_to_bq(duckdb_type: str) -> str:
    """Convert a DuckDB type name to its BigQuery (standard-SQL) equivalent.

    Handles parameterized types:
    - ``BIGINT[]`` → ``ARRAY<INT64>``
    - ``STRUCT(name VARCHAR, age BIGINT)`` → ``STRUCT<name STRING, age INT64>``

    Raises :class:`ValidationError` for unmappable types. Autodetect load
    inference uses :func:`duckdb_type_to_bq_field` instead, which maps onto
    BigQuery's legacy REST names and RECORD / REPEATED structure.
    """
    upper = duckdb_type.strip().upper()

    # Parameterized types (ARRAY suffix / LIST(...) / STRUCT(...)).
    compound = _duckdb_compound_to_bq(duckdb_type, upper)
    if compound is not None:
        return compound

    # DECIMAL with explicit precision/scale → NUMERIC or BIGNUMERIC.
    bignumeric_threshold = 38
    if upper.startswith("DECIMAL"):
        precision, _scale = _parse_decimal_params(upper)
        if precision is not None and precision > bignumeric_threshold:
            return "BIGNUMERIC"
        return "NUMERIC"

    # Direct lookup, then alias lookup (both map to non-empty BQ names).
    result = _DUCKDB_TO_BQ.get(upper) or _DUCKDB_ALIASES.get(upper)
    if result is not None:
        return result

    raise ValidationError(f"Unmappable DuckDB type: {duckdb_type!r}")


# ---------------------------------------------------------------------------
# Schema-level helpers
# ---------------------------------------------------------------------------


def bq_schema_to_duckdb_columns(
    fields: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Convert a BigQuery ``TableSchema.fields`` list to DuckDB ``(name, type)`` pairs.

    Each element of *fields* is a dict with at least ``name`` and ``type``
    keys (matching the REST ``TableFieldSchema`` shape). For RANGE
    fields, the dict additionally carries a ``rangeElementType`` /
    ``range_element_type`` sub-dict whose ``type`` field gives the inner
    element type.
    """
    result: list[tuple[str, str]] = []
    for field in fields:
        name: str = field["name"]
        bq_type: str = field["type"]
        mode: str = field.get("mode", "NULLABLE")

        # Nested STRUCT: recurse into sub-fields.
        if bq_type.upper() in ("RECORD", "STRUCT"):
            sub_fields = field.get("fields", [])
            inner_cols = bq_schema_to_duckdb_columns(sub_fields)
            inner_spec = ", ".join(f"{n} {t}" for n, t in inner_cols)
            duckdb_type = f"STRUCT({inner_spec})"
        elif bq_type.upper() == "RANGE":
            # REST shape: ``{"type":"RANGE","rangeElementType":{"type":"DATE"}}``.
            range_elem: Any = field.get("rangeElementType")
            if range_elem is None:
                range_elem = field.get("range_element_type")
            if range_elem is None:
                raise ValidationError(
                    f"RANGE field {name!r} missing rangeElementType",
                )
            elem_type = range_elem.get("type") if isinstance(range_elem, dict) else None
            if not elem_type:
                raise ValidationError(
                    f"RANGE field {name!r} rangeElementType missing 'type'",
                )
            duckdb_type = bq_to_duckdb(f"RANGE<{elem_type}>")
        else:
            duckdb_type = bq_to_duckdb(bq_type)

        # REPEATED mode wraps in a LIST.
        if mode.upper() == "REPEATED":
            duckdb_type = f"{duckdb_type}[]"

        result.append((name, duckdb_type))
    return result


def duckdb_type_to_bq_field(
    name: str,
    duckdb_type: str,
    *,
    mode: str = "NULLABLE",
) -> dict[str, Any]:
    """Convert a DuckDB column type into a BigQuery REST schema field.

    Produces the ``{"name", "type", "mode", "fields"}`` shape the REST
    ``TableFieldSchema`` uses, so the result feeds straight into
    ``_parse_schema_fields``. This is the autodetect counterpart to
    :func:`bq_schema_to_duckdb_columns` (which goes the other way): it maps
    the types DuckDB's ``read_csv_auto`` / ``read_json_auto`` infer onto
    BigQuery's schema with full structural parity:

    - scalars use BigQuery's legacy REST names (``BIGINT`` → ``INTEGER``,
      ``DOUBLE`` → ``FLOAT``, ``BOOLEAN`` → ``BOOLEAN``), matching what real
      BigQuery returns from ``tables.get``;
    - ``STRUCT(...)`` → a ``RECORD`` whose ``fields`` are converted
      recursively;
    - an array (``T[]`` or ``LIST(T)``) → the element's field with
      ``mode="REPEATED"``, so an array of struct becomes a ``REPEATED``
      ``RECORD`` and an array of scalar a ``REPEATED`` scalar.

    Raises :class:`ValidationError` for a nested array (``T[][]``), which
    BigQuery's schema model cannot represent.
    """
    stripped = duckdb_type.strip()
    upper = stripped.upper()

    element = _duckdb_array_element(stripped, upper)
    if element is not None:
        element = element.strip()
        if _duckdb_array_element(element, element.upper()) is not None:
            raise ValidationError(
                f"Cannot map nested DuckDB array type {duckdb_type!r} to a "
                "BigQuery field: BigQuery has no ARRAY of ARRAY.",
            )
        return duckdb_type_to_bq_field(name, element, mode="REPEATED")

    if upper.startswith(("STRUCT(", "STRUCT (")):
        sub_fields = _parse_struct_fields(_extract_parens(stripped, "STRUCT"))
        return {
            "name": name,
            "type": "RECORD",
            "mode": mode,
            "fields": [
                duckdb_type_to_bq_field(field_name, field_type)
                for field_name, field_type in sub_fields
            ],
        }

    standard = duckdb_to_bq(stripped)
    return {"name": name, "type": _BQ_STANDARD_TO_WIRE.get(standard, standard), "mode": mode}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _duckdb_array_element(stripped: str, upper: str) -> str | None:
    """Return the element type of a DuckDB array (``T[]`` / ``LIST(T)``), else ``None``.

    ``stripped`` is the whitespace-trimmed type string and ``upper`` its
    upper-cased form (the caller already has both, so they are passed in
    rather than recomputed).
    """
    if upper.endswith("[]"):
        return stripped[:-2].strip()
    if upper.startswith(("LIST(", "LIST (")):
        return _extract_parens(stripped, "LIST")
    return None


def _parse_struct_fields(inner: str) -> list[tuple[str, str]]:
    """Parse ``"name STRING, age INT64"`` into ``[("name", "STRING"), ...]``."""
    fields: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for char in inner:
        if char in ("<", "("):
            depth += 1
            current += char
        elif char in (">", ")"):
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            fields.append(_split_name_type(current.strip()))
            current = ""
        else:
            current += char
    if current.strip():
        fields.append(_split_name_type(current.strip()))
    return fields


def _split_name_type(token: str) -> tuple[str, str]:
    """Split ``"name STRING"`` into ``("name", "STRING")``."""
    parts = token.split(None, 1)
    if len(parts) != 2:  # noqa: PLR2004
        raise ValidationError(f"Cannot parse struct field: {token!r}")
    return parts[0], parts[1]


def _extract_parens(type_str: str, prefix: str) -> str:
    """Extract the contents between ``PREFIX(`` and the matching ``)``.

    Whitespace between the prefix keyword and ``(`` is tolerated, so both
    ``STRUCT(a INT)`` and ``STRUCT (a INT)`` parse identically.

    Example: ``_extract_parens("STRUCT(a INT, b TEXT)", "STRUCT")`` → ``"a INT, b TEXT"``.
    """
    upper = type_str.upper()
    start = upper.index("(", upper.index(prefix.upper()) + len(prefix)) + 1
    depth = 1
    pos = start
    while pos < len(type_str) and depth > 0:
        if type_str[pos] == "(":
            depth += 1
        elif type_str[pos] == ")":
            depth -= 1
        pos += 1
    return type_str[start : pos - 1]


def _parse_decimal_params(upper: str) -> tuple[int | None, int | None]:
    """Parse ``DECIMAL(P, S)`` into ``(P, S)``. Returns ``(None, None)`` if no params."""
    if "(" not in upper:
        return None, None
    inner = upper[upper.index("(") + 1 : upper.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    precision = int(parts[0]) if len(parts) >= 1 else None
    scale = int(parts[1]) if len(parts) >= 2 else None  # noqa: PLR2004
    return precision, scale


__all__ = [
    "TYPE_MAP",
    "TypeMapping",
    "bq_schema_to_duckdb_columns",
    "bq_to_duckdb",
    "duckdb_to_bq",
    "duckdb_type_to_bq_field",
]
