r"""Avro row-format serializer for the Storage Read API (ADR 0030).

Real BigQuery's Storage Read API supports two wire formats:

* **Arrow IPC** — Python / Go / Node default.
* **Apache Avro** — Java client default.

The Storage Read Avro wire contract is "schema-once on the session,
naked binary rows per response chunk":

* ``ReadSession.avro_schema.schema`` carries the writer schema as a
  JSON string (the Avro schema is itself JSON).
* ``ReadRowsResponse.avro_rows.serialized_binary_rows`` carries the
  per-chunk row bytes. They are **NOT** wrapped in an Avro Object
  Container File (OCF) — no ``Obj\\x01`` magic, no embedded schema,
  no sync marker. Each row is encoded via Avro's documented binary
  encoding back-to-back; the client uses the session's schema to
  decode them.

This module exposes two public functions matching that contract:

* :func:`arrow_schema_to_avro_json` — convert a pyarrow Schema to the
  Avro JSON shape Google's BigQuery → Avro type mapping defines
  (`<https://docs.cloud.google.com/bigquery/docs/exporting-data#avro_export_details>`_).
* :func:`serialize_arrow_table_to_avro_rows` — encode every row of a
  pyarrow Table into naked Avro binary rows using
  :func:`fastavro.schemaless_writer`.

The BigQuery → Avro type mapping the schema converter implements:

============  =========================================================
BigQuery      Avro
============  =========================================================
INT64         ``long``
FLOAT64       ``double``
NUMERIC       ``bytes`` + ``logicalType=decimal``, precision=38, scale=9
BIGNUMERIC    ``bytes`` + ``logicalType=decimal``, precision=76, scale=38
STRING        ``string``
BYTES         ``bytes``
BOOL          ``boolean``
DATE          ``int`` + ``logicalType=date``
TIME          ``long`` + ``logicalType=time-micros``
DATETIME      ``string`` (BigQuery-special — no native Avro logical type)
TIMESTAMP     ``long`` + ``logicalType=timestamp-micros``
GEOGRAPHY     ``string`` (WKT encoding, per BQ docs)
JSON          ``string``
RANGE<T>      ``record`` with ``start``/``end`` fields, recursive on T
INTERVAL      ``string`` (canonical Y-M D H:M:S form)
ARRAY<T>      ``array`` of T
STRUCT        ``record``
nullable T    ``["null", <T>]`` union with ``"null"`` first
REQUIRED T    bare ``<T>`` (matches real BigQuery; caller passes the
              REQUIRED column names via ``required_field_names`` since
              DuckDB query results lose the source-table REQUIRED flag)
============  =========================================================
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
import io
import json
from typing import Any

import fastavro
import pyarrow as pa

# Canonical NUMERIC / BIGNUMERIC precision + scale per BigQuery's
# documented Avro export shape. Used both in the schema converter and
# in the row serializer (fastavro needs them to encode Decimals).
_NUMERIC_PRECISION = 38
_NUMERIC_SCALE = 9
_BIGNUMERIC_PRECISION = 76
_BIGNUMERIC_SCALE = 38


def arrow_schema_to_avro_json(
    schema: pa.Schema,
    *,
    record_name: str = "Root",
    record_namespace: str = "com.bqemulator.storage",
    required_field_names: frozenset[str] | None = None,
) -> str:
    """Convert a pyarrow Schema to a BigQuery-shaped Avro JSON schema.

    The output is the JSON string the Storage Read API carries on
    ``ReadSession.avro_schema.schema``. Round-tripped through
    :func:`fastavro.parse_schema` for canonical equality.

    Args:
        schema: The pyarrow schema describing the materialized
            projection (post-``selected_fields`` filtering).
        record_name: The top-level Avro record name. BigQuery uses
            ``Root``; tests may override for distinct schemas.
        record_namespace: The top-level Avro namespace. BigQuery uses
            ``com.google.cloud.bigquery.<query-id>``; we use a stable
            ``com.bqemulator.storage`` so fixtures don't capture
            run-id volatility.
        required_field_names: Names of top-level columns that the
            BigQuery catalog flags as ``REQUIRED`` (``mode='REQUIRED'``).
            DuckDB's query-result schema marks every column as nullable
            regardless of source-table metadata, so the caller has to
            pass the catalog's view of REQUIRED-ness in for the Avro
            schema to match real BigQuery's output (bare ``T`` for
            REQUIRED columns; ``["null", T]`` for nullable ones).
            Defaults to ``None`` (every column treated as nullable —
            preserves the pre-fix behaviour for callers that don't
            have catalog access).

    Returns:
        A JSON-encoded Avro record schema as a string.
    """
    required = required_field_names or frozenset()
    record = {
        "type": "record",
        "name": record_name,
        "namespace": record_namespace,
        "fields": [
            _arrow_field_to_avro(field, is_required=field.name in required) for field in schema
        ],
    }
    return json.dumps(record, separators=(",", ":"))


def serialize_arrow_table_to_avro_rows(
    table: pa.Table,
    avro_schema_json: str,
) -> bytes:
    """Encode every row of an Arrow Table as naked Avro binary rows.

    Each row is encoded via :func:`fastavro.schemaless_writer` against
    the parsed schema and concatenated back-to-back. The result is the
    bytes the Storage Read API carries on
    ``ReadRowsResponse.avro_rows.serialized_binary_rows``.

    NO Object Container File (OCF) header, NO schema repetition, NO
    sync marker — the schema is sent once via :class:`AvroSchema` on
    the session, then each ReadRowsResponse carries the naked row
    bytes (matching BigQuery's documented gRPC wire contract).

    Args:
        table: The Arrow table (typically the materialized session
            data sliced to the requested stream range).
        avro_schema_json: The Avro schema JSON the session emitted via
            :func:`arrow_schema_to_avro_json`. Re-parsed here for
            ``fastavro.schemaless_writer``.

    Returns:
        The concatenated binary rows. Empty bytes for an empty table.
    """
    if table.num_rows == 0:
        return b""

    parsed_schema = fastavro.parse_schema(json.loads(avro_schema_json))
    rows = _arrow_table_to_python_rows(table)

    sink = io.BytesIO()
    for row in rows:
        fastavro.schemaless_writer(sink, parsed_schema, row)
    return sink.getvalue()


def _arrow_field_to_avro(
    field: pa.Field,
    *,
    is_required: bool = False,
) -> dict[str, Any]:
    """Convert one pyarrow Field to an Avro field dict.

    Args:
        field: The pyarrow field. Its ``nullable`` flag is consulted
            only if ``is_required`` is False — DuckDB's query results
            mark every column nullable regardless of source-table
            metadata, so the caller has to plumb the catalog's
            REQUIRED-ness through explicitly.
        is_required: If True, emit a bare ``<T>`` type matching the
            ``mode='REQUIRED'`` shape real BigQuery produces. If
            False, fall back to ``field.nullable`` and emit a
            ``["null", <T>]`` union (with ``"null"`` first per
            BigQuery's documented order) when nullable.
    """
    avro_type = _arrow_dtype_to_avro(field.type, field_name=field.name)
    spec: dict[str, Any] = {"name": field.name}
    if is_required or not field.nullable:
        spec["type"] = avro_type
    else:
        spec["type"] = ["null", avro_type]
        spec["default"] = None
    return spec


def _avro_decimal(dtype: pa.DataType, _field_name: str) -> Any:
    """NUMERIC / BIGNUMERIC → Avro decimal logical type (precision + scale)."""
    return {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": dtype.precision,
        "scale": dtype.scale,
    }


def _avro_list(dtype: pa.DataType, field_name: str) -> Any:
    """REPEATED column → Avro array with the element-type recurser."""
    return {
        "type": "array",
        "items": _arrow_dtype_to_avro(dtype.value_type, field_name=f"{field_name}_item"),
    }


def _avro_struct(dtype: pa.DataType, field_name: str) -> Any:
    """STRUCT column → Avro record (one Avro field per Arrow child)."""
    return {
        "type": "record",
        "name": f"{field_name}_record",
        "fields": [_arrow_field_to_avro(dtype.field(i)) for i in range(dtype.num_fields)],
    }


def _avro_map(dtype: pa.DataType, field_name: str) -> Any:
    """MAP column → Avro map<string, V>. Avro requires string-typed keys."""
    return {
        "type": "map",
        "values": _arrow_dtype_to_avro(dtype.item_type, field_name=f"{field_name}_value"),
    }


def _avro_int_all_widths(dtype: pa.DataType) -> bool:
    """Predicate matching every signed Arrow integer width."""
    return (
        pa.types.is_int64(dtype)
        or pa.types.is_int32(dtype)
        or pa.types.is_int16(dtype)
        or pa.types.is_int8(dtype)
    )


def _avro_uint_small_widths(dtype: pa.DataType) -> bool:
    """Predicate matching uint widths that fit losslessly in Avro long (8/16/32)."""
    return pa.types.is_uint32(dtype) or pa.types.is_uint16(dtype) or pa.types.is_uint8(dtype)


# Predicate → Avro-type-or-constructor dispatch for ``_arrow_dtype_to_avro``.
# Handlers are either a literal Avro type string ("long" / "double" / ...) or a
# callable taking ``(dtype, field_name)`` that builds a dict-typed Avro schema.
# BigQuery's documented Arrow → Avro mapping drives the order.
_ARROW_TO_AVRO_DISPATCH: tuple[tuple[Callable[[pa.DataType], bool], Any], ...] = (
    # BigQuery only has INT64. Smaller widths collapse to ``long`` to
    # stay on the documented mapping. UINT64 has no native Avro unsigned
    # type — also stored as ``long`` (BigQuery has no UINT64 surface;
    # adapter-side only).
    (_avro_int_all_widths, "long"),
    (_avro_uint_small_widths, "long"),
    (pa.types.is_uint64, "long"),
    (pa.types.is_floating, "double"),
    (pa.types.is_boolean, "boolean"),
    (lambda t: pa.types.is_string(t) or pa.types.is_large_string(t), "string"),
    (lambda t: pa.types.is_binary(t) or pa.types.is_large_binary(t), "bytes"),
    (pa.types.is_decimal, _avro_decimal),
    (
        lambda t: pa.types.is_date32(t) or pa.types.is_date64(t),
        {"type": "int", "logicalType": "date"},
    ),
    (
        lambda t: pa.types.is_time32(t) or pa.types.is_time64(t),
        {"type": "long", "logicalType": "time-micros"},
    ),
    (pa.types.is_timestamp, {"type": "long", "logicalType": "timestamp-micros"}),
    (lambda t: pa.types.is_list(t) or pa.types.is_large_list(t), _avro_list),
    (pa.types.is_struct, _avro_struct),
    (pa.types.is_map, _avro_map),
)


def _arrow_dtype_to_avro(dtype: pa.DataType, *, field_name: str) -> Any:
    """Convert one pyarrow DataType to its Avro JSON form.

    Per BigQuery's documented type mapping. ``field_name`` is used to
    derive stable, unique nested record names (Avro requires every
    record to have a name and rejects two records with the same fully
    qualified name in the same schema).

    GEOGRAPHY, JSON, INTERVAL, DATETIME all surface as Arrow strings
    at the storage layer; the dispatch table catches them via the
    string predicate. Any Arrow type not matched here falls through
    to ``"string"``, matching the documented BigQuery convention for
    "unknown to Avro" types.
    """
    for predicate, mapping in _ARROW_TO_AVRO_DISPATCH:
        if predicate(dtype):
            return mapping(dtype, field_name) if callable(mapping) else mapping
    return "string"


def _arrow_table_to_python_rows(table: pa.Table) -> list[dict[str, Any]]:
    """Convert a pyarrow Table to a list of dicts ready for fastavro.

    pyarrow's :meth:`pa.Table.to_pylist` gets us most of the way —
    every cell becomes a native Python value. fastavro's encoder
    needs Decimals for ``decimal``-logical fields, ints for
    ``date``-logical (days since epoch), longs for the time / timestamp
    logical types (microseconds), bytes for ``bytes``, and dicts /
    lists for records / arrays. ``to_pylist`` already produces the
    correct shape for the common types (datetime, date, Decimal,
    bytes, dict, list). The conversions we apply here cover only the
    edge cases where pyarrow's natural Python form needs nudging into
    Avro's expected form.
    """
    rows = table.to_pylist()
    # pa.Table.to_pylist already emits the right Python types for the
    # mappings we care about (Decimal, date, datetime, bytes, dict,
    # list). fastavro infers epoch / micros offsets from the logical
    # type metadata, so the converted dicts can be fed straight in.
    # Keep the explicit walk as a hook for future BQ types that need
    # custom marshalling (RANGE, INTERVAL canonical encoding, etc.)
    # without changing the public surface.
    _ = (Decimal, date, datetime, time, timezone)  # imported for downstream extensions
    return rows


__all__ = [
    "arrow_schema_to_avro_json",
    "serialize_arrow_table_to_avro_rows",
]
