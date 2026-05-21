"""Row + schema encoding shared by the recorder and the runner.

Both ``scripts/record_conformance_fixtures.py`` (which writes
``expected.json``) and ``tests/conformance/test_corpus.py`` (which
reads the emulator's result) call into this module so the two sides
produce JSON in exactly the same shape — without that, comparison
would chase formatting differences instead of semantic differences.

The encoder accepts a ``google.cloud.bigquery.SchemaField`` for type
context. Decimal-valued types (``NUMERIC`` / ``BIGNUMERIC``) are
serialised as strings to preserve precision through ``json.dumps``;
binary data (``BYTES``) is serialised as base64; datetime / date /
time / timestamp types are serialised in ISO 8601.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, time
import json
from typing import Any


def field_to_jsonable(field_def: Any) -> dict[str, Any]:
    """Render a ``SchemaField`` as a recorder-compatible dict.

    ``field_def`` is the ``google.cloud.bigquery.SchemaField`` object
    returned by ``QueryResult.schema``. The returned dict carries
    ``name``, ``type``, ``mode``, and (for STRUCT / RECORD fields)
    nested ``fields``.
    """
    nested = getattr(field_def, "fields", None) or ()
    fields = [field_to_jsonable(nested_field) for nested_field in nested]
    result: dict[str, Any] = {
        "name": field_def.name,
        "type": (field_def.field_type or "").upper(),
        "mode": (field_def.mode or "NULLABLE").upper(),
    }
    if fields:
        result["fields"] = fields
    return result


def row_to_jsonable(value: Any, field_def: Any) -> Any:
    """Render one cell value as JSON-friendly form.

    Type dispatch mirrors :mod:`tests.conformance._comparison`: every
    branch produces a value the comparison helper can decode without
    inferring schema from the row alone.
    """
    if value is None:
        return None

    mode = (field_def.mode or "NULLABLE").upper()
    field_type = (field_def.field_type or "").upper()

    if mode == "REPEATED":
        # The nested-field type carries the element type. We construct
        # a synthetic element schema with NULLABLE mode so the
        # recursive call doesn't loop forever.
        element_def = _element_schema(field_def)
        if isinstance(value, list):
            return [row_to_jsonable(v, element_def) for v in value]
        return [row_to_jsonable(value, element_def)]

    if field_type in {"STRUCT", "RECORD"}:
        return _struct_to_jsonable(value, field_def)

    return _scalar_to_jsonable(value, field_type)


def _element_schema(field_def: Any) -> Any:
    """Return a SchemaField-like object describing one array element."""

    class _ElementField:
        """Anonymous SchemaField proxy with mode='NULLABLE'."""

        name = field_def.name
        field_type = field_def.field_type
        mode = "NULLABLE"
        fields = field_def.fields

    return _ElementField()


def _struct_to_jsonable(value: Any, field_def: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for nested in field_def.fields or ():
        out[nested.name] = row_to_jsonable(_get_nested(value, nested.name), nested)
    return out


def _get_nested(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    # google-cloud-bigquery returns STRUCT cells as Row objects keyed
    # by field name; fall back to attribute access for safety.
    try:
        return value[name]
    except (TypeError, KeyError, IndexError):
        return getattr(value, name, None)


def _scalar_to_jsonable(value: Any, field_type: str) -> Any:
    if field_type == "BYTES":
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        if isinstance(value, str):
            return value
        return str(value)
    if field_type in {"NUMERIC", "BIGNUMERIC"}:
        # Decimal already stringifies via ``str``; the explicit branch
        # documents the contract that NUMERIC values round-trip as
        # canonical decimal strings, never floats.
        return str(value)
    if field_type == "TIMESTAMP":
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    if field_type == "DATETIME":
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    if field_type == "DATE":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    if field_type == "TIME":
        if isinstance(value, time):
            return value.isoformat()
        return str(value)
    if field_type in {"GEOGRAPHY", "STRING", "INTERVAL"}:
        return str(value)
    if field_type == "JSON":
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, sort_keys=True)
        except TypeError:
            return str(value)
    if field_type == "RANGE":
        return _range_to_jsonable(value)
    # INT64 / BOOL / FLOAT64 → JSON-native types pass through.
    if isinstance(value, (int, float, bool)):
        return value
    return value


def _range_to_jsonable(value: Any) -> Any:
    """Encode a RANGE cell as a ``{"start": ..., "end": ...}`` dict.

    BigQuery returns RANGE values as a dict-like ``Row`` with ``start``
    and ``end`` fields; we recurse so the inner DATE / DATETIME /
    TIMESTAMP gets the same ISO 8601 encoding the comparison helper
    expects.
    """
    if value is None:
        return None
    start = _get_nested(value, "start")
    end = _get_nested(value, "end")

    def _encode(inner: Any) -> Any:
        if isinstance(inner, datetime):
            return inner.isoformat()
        if isinstance(inner, date):
            return inner.isoformat()
        if inner is None:
            return None
        return str(inner)

    return {"start": _encode(start), "end": _encode(end)}


__all__ = ["field_to_jsonable", "row_to_jsonable"]
