"""Convert ``parameters.json`` payloads to BigQuery ``QueryParameter`` objects.

Both the conformance recorder (``scripts/record_conformance_fixtures.py``)
and the conformance runner (``tests/conformance/test_corpus.py``) call
:func:`build_query_parameters` to turn the on-disk JSON shape into a list
of ``google.cloud.bigquery.QueryParameter`` instances. The recorder
passes the list to real BigQuery via ``QueryJobConfig.query_parameters``;
the runner passes the same list to the BQ Python client against the
emulator's REST endpoint, so the wire-format ``queryParameters`` body
field is exercised end-to-end.

The on-disk shape is documented in ``sql_corpus/README.md``. Two modes
are supported:

* ``"named"`` — every entry carries a ``name``; the BQ client renders
  the parameters as ``@<name>`` placeholders in the SQL.
* ``"positional"`` — entries do NOT carry a ``name``; placeholders in
  the SQL are ``?`` and bound in order.

Type spec shapes:

* Scalar: ``"type": "INT64"`` (or ``"STRING"``, ``"NUMERIC"``, etc.).
* Array : ``"type": {"type": "ARRAY", "arrayType": {"type": "<scalar>"}}``.
* Struct: ``"type": {"type": "STRUCT", "structTypes": [{"name": "x",
  "type": "<scalar-or-compound>"}, ...]}``.

Values follow the BQ Python client's conventions: scalars are passed
through; arrays are lists; structs are dicts keyed by field name. ``null``
in JSON becomes Python ``None`` which the BQ client encodes as a
``parameterValue`` with no ``value`` key (i.e. a typed NULL).
"""

from __future__ import annotations

from typing import Any

_SCALAR_TYPES: frozenset[str] = frozenset(
    {
        "BOOL",
        "BOOLEAN",
        "BYTES",
        "DATE",
        "DATETIME",
        "FLOAT64",
        "FLOAT",
        "GEOGRAPHY",
        "INT64",
        "INTEGER",
        "INTERVAL",
        "JSON",
        "NUMERIC",
        "BIGNUMERIC",
        "STRING",
        "TIME",
        "TIMESTAMP",
    },
)


def build_query_parameters(payload: dict[str, Any]) -> list[Any]:
    """Convert a ``parameters.json`` payload to BQ ``QueryParameter`` objects.

    Returns a list ready for ``QueryJobConfig(query_parameters=...)``.
    The mode (named vs positional) is read from the payload and
    enforced by the BQ client when the job is submitted — positional
    entries pass ``name=None`` to the parameter constructors.

    Raises:
        TypeError: when the payload shape is invalid (e.g. ``parameters``
            is not a list).
        ValueError: when a parameter entry references an unknown type
            kind (the recorder rejects this before submitting the job;
            the runner rejects this before serialising the REST body).
    """
    from google.cloud import bigquery

    mode = payload["mode"]
    raw_params = payload["parameters"]
    if not isinstance(raw_params, list):
        msg = f"parameters must be a list (got {type(raw_params).__name__})"
        raise TypeError(msg)

    result: list[Any] = []
    for entry in raw_params:
        if not isinstance(entry, dict):
            msg = f"parameter entry must be a dict (got {type(entry).__name__})"
            raise TypeError(msg)
        name = entry.get("name") if mode == "named" else None
        type_spec = entry["type"]
        value = entry.get("value")
        result.append(_build_one(name, type_spec, value, bigquery))
    return result


def _build_one(
    name: str | None,
    type_spec: Any,
    value: Any,
    bigquery: Any,
) -> Any:
    """Build one ``QueryParameter`` of the appropriate sub-class.

    ``name=None`` produces a positional parameter; the BQ Python client
    accepts ``None`` as the name for every parameter kind and renders
    the SQL ``?`` placeholders in submission order.
    """
    if isinstance(type_spec, str):
        upper = type_spec.upper()
        if upper not in _SCALAR_TYPES:
            msg = f"Unknown scalar type: {type_spec!r}"
            raise ValueError(msg)
        return bigquery.ScalarQueryParameter(name, upper, value)

    if isinstance(type_spec, dict):
        kind = type_spec.get("type", "").upper()
        if kind == "ARRAY":
            element_spec = type_spec.get("arrayType")
            element_type_name = _element_type_name(element_spec)
            return bigquery.ArrayQueryParameter(name, element_type_name, value or [])
        if kind == "STRUCT":
            struct_types = type_spec.get("structTypes", [])
            sub_params: list[Any] = []
            value_dict = value if isinstance(value, dict) else {}
            for st in struct_types:
                if not isinstance(st, dict):
                    msg = f"STRUCT field spec must be a dict (got {type(st).__name__})"
                    raise TypeError(msg)
                sub_name = st["name"]
                sub_type = st["type"]
                sub_value = value_dict.get(sub_name)
                sub_params.append(_build_one(sub_name, sub_type, sub_value, bigquery))
            return bigquery.StructQueryParameter(name, *sub_params)
        msg = f"Unknown compound type kind: {kind!r}"
        raise ValueError(msg)

    msg = f"Parameter type must be str or dict (got {type(type_spec).__name__})"
    raise TypeError(msg)


def _element_type_name(element_spec: Any) -> str:
    """Extract the scalar element type name from an ARRAY's ``arrayType``.

    P2.e arrays are always over a scalar element type — the BQ Python
    client's ``ArrayQueryParameter`` takes a scalar type name (e.g.
    ``"INT64"``) for the element. Struct-element arrays would require
    ``bigquery.StructQueryParameterType``; intentionally out of scope
    for the P2.e fixture set.
    """
    if isinstance(element_spec, str):
        upper = element_spec.upper()
        if upper not in _SCALAR_TYPES:
            msg = f"Unknown array element type: {element_spec!r}"
            raise ValueError(msg)
        return upper
    if isinstance(element_spec, dict):
        nested = element_spec.get("type")
        if not isinstance(nested, str):
            msg = "Array element type dict must carry a string 'type'"
            raise TypeError(msg)
        upper = nested.upper()
        if upper not in _SCALAR_TYPES:
            msg = f"Unknown array element type: {nested!r}"
            raise ValueError(msg)
        return upper
    msg = f"Array element type must be str or dict (got {type(element_spec).__name__})"
    raise TypeError(msg)


__all__ = ["build_query_parameters"]
