"""Avro file → Arrow table bridge for the load executor (G1 follow-up).

Used as a fallback when DuckDB's native ``read_avro`` can't handle a
particular Avro feature — in particular the ``decimal`` logical type,
which DuckDB 1.5 returns as a ``BLOB`` column whose auto-cast to a
NUMERIC destination fails with ``Conversion Error: Unimplemented
type for cast (BLOB -> DECIMAL(p, s))``. ``fastavro`` decodes the
logical-type to Python ``Decimal`` directly, so we route through it
and bulk-insert via DuckDB's ``connection.register(...)`` →
``INSERT INTO ... SELECT * FROM <view>`` (the same pattern
:mod:`bqemulator.jobs.orc_reader` uses).

Other Avro logical types (``date``, ``timestamp-millis``,
``timestamp-micros``, ``uuid``) work cleanly via DuckDB's native
handling, so the fallback only fires on the narrow decimal-shaped
slice that DuckDB doesn't yet support. See ADR 0027 §"Avro logical-
type coverage" + the G1-follow-up closure of
[`out-of-scope.md#avro-decimal-logical-type-load`](../reference/out-of-scope.md).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from bqemulator.domain.errors import InvalidQueryError, UnsupportedFeatureError

if TYPE_CHECKING:
    import pyarrow as pa


def read_avro_to_arrow(path: str) -> pa.Table:
    """Read an Avro file into a :class:`pyarrow.Table` via fastavro.

    fastavro is already a hard dependency (declared under the
    ``[avro]`` optional extra used at runtime by tests; the ``[all]``
    extra rolls it in). Unlike DuckDB's native ``read_avro``, fastavro
    decodes the ``decimal`` logical type to Python :class:`Decimal`
    directly, which makes the Arrow round-trip lossless against the
    canonical recorded BigQuery shape.

    Raises:
        UnsupportedFeatureError: if ``fastavro`` is not installed.
        InvalidQueryError: if the file is missing or unparseable.
    """
    try:
        import fastavro
    except ImportError as exc:
        raise UnsupportedFeatureError(
            "Load from AVRO via the fastavro fallback requires the "
            "optional ``fastavro`` dependency. Install bqemulator with "
            "the ``[avro]`` extra (``pip install 'bqemulator[avro]'``) "
            "or with ``[all]``.",
        ) from exc

    import pyarrow as pa

    try:
        with open(path, "rb") as fh:  # noqa: PTH123 — fastavro needs a binary stream
            reader = fastavro.reader(fh)
            writer_schema = reader.writer_schema
            rows = list(reader)
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise InvalidQueryError(f"Avro file not found: {path}") from exc
    except Exception as exc:
        raise InvalidQueryError(f"Failed to read Avro file {path}: {exc}") from exc

    arrow_schema = _avro_schema_to_arrow(writer_schema)
    column_arrays: dict[str, list[Any]] = {field.name: [] for field in arrow_schema}
    for row in rows:
        if not isinstance(row, dict):
            msg = f"Expected Avro record-of-records; got {type(row).__name__}"
            raise InvalidQueryError(msg)
        for field in arrow_schema:
            column_arrays[field.name].append(row.get(field.name))

    return pa.table(
        {
            field.name: pa.array(column_arrays[field.name], type=field.type)
            for field in arrow_schema
        },
        schema=arrow_schema,
    )


def _avro_schema_to_arrow(avro_schema: Any) -> pa.Schema:
    """Convert an Avro record schema to a pyarrow Schema.

    Only the surface needed to round-trip BigQuery-shape Avro files is
    handled: primitives + logical types (``decimal``, ``date``,
    ``timestamp-millis``, ``timestamp-micros``) and nullable unions
    (``["null", "<T>"]``). Records-of-records map to pyarrow structs;
    arrays map to lists. Unknown shapes fall back to string so the
    load doesn't silently drop data.
    """
    import pyarrow as pa

    if not isinstance(avro_schema, dict) or avro_schema.get("type") != "record":
        msg = f"Top-level Avro schema must be a record; got {avro_schema!r}"
        raise InvalidQueryError(msg)
    fields = [
        pa.field(field["name"], _avro_field_to_arrow(field["type"]))
        for field in avro_schema.get("fields", [])
    ]
    return pa.schema(fields)


def _avro_field_to_arrow(avro_type: Any) -> pa.DataType:
    """Convert a single Avro field-type spec to an Arrow type."""
    import pyarrow as pa

    if isinstance(avro_type, list):
        # Nullable union: ["null", "<T>"] — Arrow types are always
        # nullable so we drop the "null" branch and recurse.
        non_null = [t for t in avro_type if t != "null"]
        if len(non_null) == 1:
            return _avro_field_to_arrow(non_null[0])
        # Multi-type unions are rare in BigQuery exports; fall back to string.
        return pa.string()

    if isinstance(avro_type, dict):
        logical = avro_type.get("logicalType")
        base = avro_type.get("type")
        if logical == "decimal":
            precision = int(avro_type.get("precision", 38))
            scale = int(avro_type.get("scale", 9))
            return pa.decimal128(precision, scale)
        if logical == "date":
            return pa.date32()
        if logical in {"timestamp-millis", "timestamp-micros"}:
            return pa.timestamp("us", tz="UTC")
        if base == "record":
            sub_fields = [
                pa.field(field["name"], _avro_field_to_arrow(field["type"]))
                for field in avro_type.get("fields", [])
            ]
            return pa.struct(sub_fields)
        if base == "array":
            return pa.list_(_avro_field_to_arrow(avro_type["items"]))
        if base == "map":
            # Avro maps are <string, V>; pyarrow models as map<string, V>.
            return pa.map_(pa.string(), _avro_field_to_arrow(avro_type["values"]))
        if base == "bytes":
            return pa.binary()
        # Recursively resolve the nested type.
        return _avro_field_to_arrow(base)

    # String-form primitives.
    return {
        "boolean": pa.bool_(),
        "int": pa.int32(),
        "long": pa.int64(),
        "float": pa.float32(),
        "double": pa.float64(),
        "bytes": pa.binary(),
        "string": pa.string(),
        "null": pa.null(),
    }.get(avro_type, pa.string())


def is_decimal_logical_avro(path: str) -> bool:
    """Return True if the Avro file's writer schema contains a ``decimal`` logical type.

    Cheap pre-check so the executor knows whether to skip DuckDB's
    ``read_avro`` (which would fail) and route directly through the
    fastavro fallback. Falls back to ``False`` on any read error;
    the executor's own try/except handles the genuine read failure.
    """
    try:
        import fastavro

        with open(path, "rb") as fh:  # noqa: PTH123
            reader = fastavro.reader(fh)
            schema = reader.writer_schema
    except Exception:  # noqa: BLE001 — pre-check only
        return False
    return _schema_has_decimal_logical(schema)


def _schema_has_decimal_logical(schema: Any) -> bool:
    """Recurse through an Avro schema, return True if any node is ``logicalType: decimal``."""
    if isinstance(schema, dict):
        if schema.get("logicalType") == "decimal":
            return True
        if "fields" in schema:
            return any(_schema_has_decimal_logical(f.get("type")) for f in schema["fields"])
        if "items" in schema:
            return _schema_has_decimal_logical(schema["items"])
        if "values" in schema:
            return _schema_has_decimal_logical(schema["values"])
        if "type" in schema:
            return _schema_has_decimal_logical(schema["type"])
    if isinstance(schema, list):
        return any(_schema_has_decimal_logical(s) for s in schema)
    return False


# Re-exports used only to keep mypy happy in callers that don't have
# pyarrow imported at the top level.
__all__ = [
    "Decimal",
    "date",
    "datetime",
    "is_decimal_logical_avro",
    "read_avro_to_arrow",
]
