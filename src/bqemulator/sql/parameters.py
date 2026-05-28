"""Query parameter binding.

BigQuery supports positional (``?``) and named (``@name``) parameters.
The REST API sends them in ``QueryRequest.queryParameters`` as typed
values.

This module:

1. Converts BigQuery parameter values to Python types that DuckDB's
   prepared-statement interface accepts.
2. Replaces ``@name`` placeholders with ``?`` positional markers and
   returns the ordered parameter list.
3. Wraps NULL-valued parameters in ``CAST(? AS <duckdb-type>)`` so the
   BigQuery schema renderer surfaces the declared type rather than
   DuckDB's default-inferred type (which falls back to BIGINT for a
   bare NULL parameter regardless of the BQ-declared type).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import re
from typing import Any


def bind_parameters(
    sql: str,
    query_parameters: list[dict[str, Any]] | None = None,
) -> tuple[str, list[Any]]:
    """Replace BigQuery parameters in *sql* and return DuckDB-ready values.

    Args:
        sql: The DuckDB SQL (already translated from BigQuery).
        query_parameters: BigQuery REST ``QueryParameter`` list. Each
            element has ``parameterType`` and ``parameterValue``. Named
            parameters also have a ``name`` key. Positional parameters
            are applied in order.

    Returns:
        ``(sql_with_placeholders, parameter_values)`` ready for
        ``duckdb.execute(sql, params)``.
    """
    if not query_parameters:
        return sql, []

    # Detect whether these are positional or named.
    first = query_parameters[0]
    if first.get("name"):
        return _bind_named(sql, query_parameters)
    return _bind_positional(sql, query_parameters)


def _placeholder_for(value: Any, param: dict[str, Any]) -> str:
    """Return the DuckDB placeholder text for *value* with type hint.

    A non-NULL scalar / array / struct returns a bare ``?``. A NULL
    scalar (``value is None``) is wrapped in ``CAST(? AS <duckdb-type>)``
    so DuckDB knows the declared BQ type even though the bound Python
    value is ``None``. Without the wrap, DuckDB defaults the ``?``
    column type to BIGINT regardless of the BQ-declared type, which
    drifts the BigQuery REST schema response.
    """
    if value is not None:
        return "?"
    duckdb_type = _bq_to_duckdb_type(param.get("parameterType", {}))
    if duckdb_type is None:
        return "?"
    return f"CAST(? AS {duckdb_type})"


def _bind_positional(
    sql: str,
    params: list[dict[str, Any]],
) -> tuple[str, list[Any]]:
    """Keep / rewrite ``?`` markers and return prepared values.

    DuckDB uses ``?`` for positional parameters natively, so the SQL
    already has them. Where a parameter is NULL we walk the markers in
    order and replace each NULL-bound ``?`` with ``CAST(? AS T)`` so
    the column type matches the BigQuery-declared parameter type. The
    walk respects ``?`` characters that appear inside string literals
    (e.g. ``WHERE x = 'wat?'``) — those are not rewritten.
    """
    values = [_extract_value(p) for p in params]
    if not any(v is None for v in values):
        return sql, values

    # Walk markers in order and rewrite NULL slots. The iterator is
    # advanced lock-step with the regex callback so the Nth bare ``?``
    # gets the Nth parameter's type — assertion: the BQ wire-format
    # parameter count equals the bare ``?`` count in the SQL (BigQuery
    # itself enforces this at submission time).
    idx_iter = iter(range(len(params)))

    def _replace(_match: re.Match[str]) -> str:
        idx = next(idx_iter)
        return _placeholder_for(values[idx], params[idx])

    result = _replace_question_marks(sql, _replace)
    return result, values


def _bind_named(
    sql: str,
    params: list[dict[str, Any]],
) -> tuple[str, list[Any]]:
    """Replace named parameters with ``?`` (DuckDB positional) and return values.

    Matches both ``@name`` (BigQuery notation) and ``$name`` (SQLGlot's
    DuckDB transpilation output). The ``$`` form is what we actually see
    after SQLGlot processes the query. NULL-valued parameters are
    wrapped in ``CAST(? AS <duckdb-type>)`` so the schema renderer
    surfaces the declared BQ type.
    """
    name_to_param: dict[str, dict[str, Any]] = {p["name"]: p for p in params}
    name_to_value: dict[str, Any] = {n: _extract_value(p) for n, p in name_to_param.items()}

    values: list[Any] = []

    def _replacer(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in name_to_value:
            value = name_to_value[name]
            values.append(value)
            return _placeholder_for(value, name_to_param[name])
        # Not a known parameter — leave it alone (might be a DuckDB
        # system variable like $1, $2 from a different source).
        return match.group(0)

    # Match both @name (BigQuery) and $name (SQLGlot-transpiled DuckDB).
    result = re.sub(r"[@$](\w+)", _replacer, sql)
    return result, values


def _replace_question_marks(sql: str, replace: Any) -> str:
    """Walk ``sql`` and call ``replace`` on every bare ``?`` outside string literals.

    DuckDB's lexer treats ``?`` inside ``'…'`` and ``"…"`` as literal
    text, not a parameter marker, so the rewriter must respect string
    boundaries when wrapping NULL-typed positional parameters. The
    rewriter is intentionally minimal — single-line single-quoted and
    double-quoted strings are tracked; doc-strings (triple-quoted) and
    backticks are not used in the post-SQLGlot DuckDB output.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in {"'", '"'}:
            # Skip through the string literal — DuckDB uses '' (doubled
            # quote) for embedded single quotes; the same applies to
            # double quotes when used for identifier quoting.
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        out.append(quote * 2)
                        i += 2
                        continue
                    out.append(quote)
                    i += 1
                    break
                if sql[i] == "\\" and i + 1 < n:
                    out.append(sql[i : i + 2])
                    i += 2
                    continue
                out.append(sql[i])
                i += 1
            continue
        if ch == "?":
            match = re.match(r"\?", sql[i:])
            out.append(replace(match))
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Mapping of BigQuery type names to DuckDB types used for the NULL-cast
# wrap. Only the BQ scalar / compound forms that can land in a query
# parameter are listed — interval / range parameters are not in the
# BigQuery query-parameter surface.
_BQ_SCALAR_TO_DUCKDB: dict[str, str] = {
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "BYTES": "BLOB",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "FLOAT64": "DOUBLE",
    "FLOAT": "DOUBLE",
    "GEOGRAPHY": "GEOMETRY",
    "INT64": "BIGINT",
    "INTEGER": "BIGINT",
    "JSON": "JSON",
    "NUMERIC": "DECIMAL(38,9)",
    "BIGNUMERIC": "DECIMAL(38,38)",
    "STRING": "VARCHAR",
    "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP WITH TIME ZONE",
}


def _bq_to_duckdb_type(parameter_type: dict[str, Any]) -> str | None:
    """Translate a BigQuery ``parameterType`` payload to a DuckDB type.

    Returns ``None`` for compound types whose DuckDB-side cast would be
    cumbersome (STRUCT) or for unknown kinds — the caller falls back to
    a bare ``?`` in those rare cases. Arrays use DuckDB's ``T[]``
    list-type form.
    """
    kind = str(parameter_type.get("type", "STRING")).upper()
    if kind == "ARRAY":
        element_type = parameter_type.get("arrayType", {}) or {}
        element_kind = _bq_to_duckdb_type(element_type)
        if element_kind is None:
            return None
        return f"{element_kind}[]"
    if kind == "STRUCT":
        return None
    return _BQ_SCALAR_TO_DUCKDB.get(kind)


def _extract_value(param: dict[str, Any]) -> Any:
    """Extract a Python value from a BigQuery QueryParameter dict.

    The ``parameterType.type`` field names the BigQuery type; the
    ``parameterValue.value`` is always a string (for scalars) or a
    nested structure (for arrays/structs).
    """
    ptype = param.get("parameterType", {})
    pvalue = param.get("parameterValue", {})
    type_kind = ptype.get("type", "STRING").upper()

    # ARRAY / STRUCT don't use "value" — they have nested structures.
    # Handle them BEFORE the scalar null-check.
    if type_kind == "ARRAY":
        return _extract_array_value(ptype, pvalue)
    if type_kind == "STRUCT":
        return _extract_struct_value(ptype, pvalue)

    # Scalar types — "value" holds the raw string value.
    raw = pvalue.get("value")
    if raw is None:
        return None
    converter = _SCALAR_CONVERTERS.get(type_kind)
    if converter is not None:
        return converter(raw)
    # All other types (STRING, BYTES, JSON, GEOGRAPHY, INTERVAL, RANGE)
    # are passed as strings — DuckDB handles the cast in the query itself.
    return str(raw)


def _extract_array_value(ptype: dict[str, Any], pvalue: dict[str, Any]) -> list[Any]:
    """Recursively extract each element of an ARRAY parameter."""
    array_values = pvalue.get("arrayValues", [])
    element_type = ptype.get("arrayType", {})
    return [
        _extract_value({"parameterType": element_type, "parameterValue": av}) for av in array_values
    ]


def _extract_struct_value(
    ptype: dict[str, Any],
    pvalue: dict[str, Any],
) -> dict[str, Any]:
    """Recursively extract each named field of a STRUCT parameter."""
    struct_values = pvalue.get("structValues", {})
    struct_types = ptype.get("structTypes", [])
    result: dict[str, Any] = {}
    for st in struct_types:
        field_name = st.get("name", "")
        field_type = st.get("type", {})
        field_value = struct_values.get(field_name, {})
        result[field_name] = _extract_value(
            {"parameterType": field_type, "parameterValue": field_value},
        )
    return result


def _coerce_bool(raw: Any) -> bool:
    """Coerce a BigQuery BOOL/BOOLEAN parameter to ``bool``."""
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in ("true", "1")


def _coerce_numeric(raw: Any) -> Decimal:
    """Coerce a BigQuery NUMERIC/BIGNUMERIC parameter to ``Decimal``."""
    return Decimal(str(raw))


def _coerce_date(raw: Any) -> date:
    """Coerce a BigQuery DATE parameter (``YYYY-MM-DD``) to ``date``."""
    return date.fromisoformat(str(raw))


def _coerce_datetime(raw: Any) -> datetime:
    """Coerce a BigQuery DATETIME parameter (``YYYY-MM-DD HH:MM:SS[.ffffff]``)."""
    text = str(raw).replace(" ", "T", 1)
    return datetime.fromisoformat(text)


def _coerce_time(raw: Any) -> time:
    """Coerce a BigQuery TIME parameter (``HH:MM:SS[.ffffff]``) to ``time``."""
    return time.fromisoformat(str(raw))


def _coerce_timestamp(raw: Any) -> datetime:
    """Coerce a BigQuery TIMESTAMP parameter, normalising ``Z`` to ``+00:00``."""
    text = str(raw)
    # BigQuery wire-format accepts a trailing 'Z' for UTC; Python
    # 3.11+ ``fromisoformat`` only accepts ``+00:00``.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T", 1)
    return datetime.fromisoformat(text)


#: BigQuery scalar type name → Python coercion function. Date/time
#: parameters MUST be converted to typed Python objects (not strings)
#: because DuckDB infers prepared-statement parameter column types
#: from the Python value's type — a plain string binds as VARCHAR,
#: which the BigQuery schema renderer surfaces as ``STRING`` rather
#: than the declared BigQuery type. Passing a typed object preserves
#: the column type all the way through to the wire-format response.
_SCALAR_CONVERTERS: dict[str, Any] = {
    "INT64": int,
    "INTEGER": int,
    "FLOAT64": float,
    "FLOAT": float,
    "BOOL": _coerce_bool,
    "BOOLEAN": _coerce_bool,
    "NUMERIC": _coerce_numeric,
    "BIGNUMERIC": _coerce_numeric,
    "DATE": _coerce_date,
    "DATETIME": _coerce_datetime,
    "TIME": _coerce_time,
    "TIMESTAMP": _coerce_timestamp,
}


__all__ = ["bind_parameters"]
