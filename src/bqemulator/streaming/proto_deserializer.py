"""Dynamic protobuf deserializer for Storage Write API.

The client sends its row schema inline as a ``DescriptorProto`` in the
first ``AppendRowsRequest`` of a connection. We build a dynamic message
class from that descriptor and decode each serialized row into a dict
keyed by column name, which is then converted to a :class:`pyarrow.Table`
matching the target table's schema.

Why dynamic? We don't know the rows' field names or types at build time
— they vary per table. Using :class:`google.protobuf.descriptor_pool.DescriptorPool`
+ :func:`google.protobuf.message_factory.GetMessageClass` lets us parse
any schema the client sends without pre-generating stubs.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message
import pyarrow as pa


class ProtoRowDecoder:
    """Build and cache a dynamic protobuf message class for a writer schema.

    One decoder instance is created per connection (per AppendRows bidi
    stream) and reused for every row on that connection. Subsequent
    messages on the same connection may omit ``writer_schema``; the
    servicer keeps the decoder alive across them.
    """

    def __init__(self, descriptor_proto: descriptor_pb2.DescriptorProto) -> None:
        self._pool = descriptor_pool.DescriptorPool()
        file_proto = descriptor_pb2.FileDescriptorProto()
        # Give the file a unique name so multiple decoders in one process
        # don't collide on pool internals.
        file_name = f"bqemu_dynamic_{uuid4().hex}.proto"
        file_proto.name = file_name
        file_proto.syntax = "proto2"
        file_proto.message_type.add().CopyFrom(descriptor_proto)

        # ``Add`` returns a FileDescriptor in cpp-backed builds and ``None`` in
        # pure-Python builds — look up via ``FindFileByName`` to be portable.
        self._pool.Add(file_proto)
        file_descriptor = self._pool.FindFileByName(file_name)
        self._descriptor = file_descriptor.message_types_by_name[descriptor_proto.name]
        self._message_class = message_factory.GetMessageClass(self._descriptor)
        self._field_names: tuple[str, ...] = tuple(field.name for field in self._descriptor.fields)

    @property
    def field_names(self) -> tuple[str, ...]:
        """Field names declared by the writer schema, in declaration order."""
        return self._field_names

    def decode(self, serialized_row: bytes) -> dict[str, Any]:
        """Parse one serialized proto row into a ``{field_name: value}`` dict."""
        message: Message = self._message_class()
        message.ParseFromString(serialized_row)
        return _message_to_dict(message, self._descriptor)


def _message_to_dict(
    message: Message,
    msg_descriptor: Any,
) -> dict[str, Any]:
    """Convert a decoded protobuf message into a plain Python dict.

    Repeated fields become ``list``; submessages recurse; bytes/enums/etc.
    are returned as Python primitives matching what pyarrow can ingest.
    Scalar fields absent from the wire are set to the message's default
    (None for submessages, 0/"" etc. for primitives) — matching real
    BigQuery's behaviour of treating unset fields as NULL when inserting
    into a NULLABLE column and the proto2 default when the column is
    REQUIRED.
    """
    result: dict[str, Any] = {}
    for field in msg_descriptor.fields:
        name = field.name
        if field.is_repeated:
            values = getattr(message, name)
            result[name] = [_decode_field_value(field, v) for v in values]
        elif field.type == FieldDescriptor.TYPE_MESSAGE:
            # HasField works for singular message fields in proto2/proto3.
            if message.HasField(name):
                result[name] = _decode_field_value(field, getattr(message, name))
            else:
                result[name] = None
        elif field.has_presence:
            # proto2 optional / proto3 oneof / proto3 optional — NULL when absent.
            if message.HasField(name):
                result[name] = _decode_field_value(field, getattr(message, name))
            else:
                result[name] = None
        else:
            # proto3 implicit scalar — no presence tracking; just take the value.
            result[name] = _decode_field_value(field, getattr(message, name))
    return result


def _decode_field_value(field: FieldDescriptor, value: Any) -> Any:
    """Convert one protobuf value to a pyarrow-friendly Python value."""
    if field.type == FieldDescriptor.TYPE_MESSAGE:
        # Nested message → recurse.
        return _message_to_dict(value, field.message_type)
    if field.type == FieldDescriptor.TYPE_BYTES:
        return bytes(value)
    if field.type == FieldDescriptor.TYPE_ENUM:
        # Return the integer ordinal; BigQuery stores enums as INT64 or STRING
        # depending on the column type — the type-map layer handles coercion.
        return int(value)
    if field.type == FieldDescriptor.TYPE_STRING:
        return str(value)
    if field.type in {
        FieldDescriptor.TYPE_INT32,
        FieldDescriptor.TYPE_INT64,
        FieldDescriptor.TYPE_UINT32,
        FieldDescriptor.TYPE_UINT64,
        FieldDescriptor.TYPE_SINT32,
        FieldDescriptor.TYPE_SINT64,
        FieldDescriptor.TYPE_FIXED32,
        FieldDescriptor.TYPE_FIXED64,
        FieldDescriptor.TYPE_SFIXED32,
        FieldDescriptor.TYPE_SFIXED64,
    }:
        return int(value)
    if field.type in {FieldDescriptor.TYPE_DOUBLE, FieldDescriptor.TYPE_FLOAT}:
        return float(value)
    if field.type == FieldDescriptor.TYPE_BOOL:
        return bool(value)
    return value


def proto_rows_to_arrow_table(
    decoder: ProtoRowDecoder,
    serialized_rows: list[bytes],
    target_schema: pa.Schema,
) -> pa.Table:
    """Convert a list of serialized proto rows into a typed Arrow table.

    Args:
        decoder: A :class:`ProtoRowDecoder` built for the writer schema.
        serialized_rows: The ``proto_rows.serialized_rows`` list from
            one ``AppendRowsRequest``.
        target_schema: The Arrow schema of the target table — drives
            column order and type coercion.

    Returns:
        A :class:`pyarrow.Table` matching ``target_schema``.
    """
    from bqemulator.storage.arrow_bridge import _coerce_to_arrow_value

    # Build column-major arrays; missing columns become NULL.
    columns: dict[str, list[Any]] = {f.name: [] for f in target_schema}

    for raw in serialized_rows:
        row = decoder.decode(raw)
        for field in target_schema:
            raw_val = row.get(field.name)
            columns[field.name].append(_coerce_to_arrow_value(raw_val, field.type))

    arrays = {field.name: pa.array(columns[field.name], type=field.type) for field in target_schema}
    return pa.table(arrays, schema=target_schema)


__all__ = [
    "ProtoRowDecoder",
    "proto_rows_to_arrow_table",
]
