"""Unit tests for :mod:`bqemulator.streaming.proto_deserializer`."""

from __future__ import annotations

from google.protobuf import descriptor_pb2
import pyarrow as pa
import pytest

from bqemulator.streaming.proto_deserializer import (
    ProtoRowDecoder,
    proto_rows_to_arrow_table,
)

pytestmark = pytest.mark.unit


def _row_descriptor() -> descriptor_pb2.DescriptorProto:
    """Build a DescriptorProto representing ``{id: int64, name: string}``."""
    msg = descriptor_pb2.DescriptorProto()
    msg.name = "Row"
    f1 = msg.field.add()
    f1.name = "id"
    f1.number = 1
    f1.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    f1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f2 = msg.field.add()
    f2.name = "name"
    f2.number = 2
    f2.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    f2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    return msg


def _tagged_varint(field_number: int, value: int) -> bytes:
    """Hand-encode a varint field — avoids round-tripping through proto-plus."""
    tag = (field_number << 3) | 0
    out = bytearray()
    out.append(tag)
    # varint encode
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def _tagged_string(field_number: int, value: str) -> bytes:
    """Hand-encode a length-delimited string field."""
    tag = (field_number << 3) | 2
    data = value.encode("utf-8")
    return bytes([tag, len(data), *data])


def _encode_row(row_id: int | None, name: str | None) -> bytes:
    out = bytearray()
    if row_id is not None:
        out += _tagged_varint(1, row_id)
    if name is not None:
        out += _tagged_string(2, name)
    return bytes(out)


class TestProtoRowDecoder:
    def test_decodes_scalar_row(self) -> None:
        """Decoding a simple proto row yields the expected dict."""
        decoder = ProtoRowDecoder(_row_descriptor())
        row = decoder.decode(_encode_row(42, "alice"))
        assert row == {"id": 42, "name": "alice"}

    def test_unset_fields_are_none(self) -> None:
        """Unset proto2-optional fields decode to ``None`` (NULL-safe)."""
        decoder = ProtoRowDecoder(_row_descriptor())
        row = decoder.decode(_encode_row(None, None))
        assert row == {"id": None, "name": None}

    def test_field_names_exposed(self) -> None:
        """``field_names`` mirrors declaration order for downstream use."""
        decoder = ProtoRowDecoder(_row_descriptor())
        assert decoder.field_names == ("id", "name")


def _all_types_descriptor() -> descriptor_pb2.DescriptorProto:
    """DescriptorProto covering every scalar + repeated + nested type path."""
    msg = descriptor_pb2.DescriptorProto()
    msg.name = "AllTypes"

    # int64, string, double, bool, bytes, enum, repeated int64
    specs = [
        (
            "i64",
            1,
            descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
        (
            "s",
            2,
            descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
        (
            "d",
            3,
            descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
        (
            "b",
            4,
            descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
        (
            "by",
            5,
            descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
        (
            "reps",
            6,
            descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
            descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
        ),
        (
            "f",
            7,
            descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
            descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        ),
    ]
    for name, number, type_, label in specs:
        fd = msg.field.add()
        fd.name = name
        fd.number = number
        fd.type = type_
        fd.label = label
    return msg


class TestAllScalarTypes:
    def test_decodes_every_scalar_type(self) -> None:
        """Decoder round-trips ints, strings, doubles, bools, bytes, floats, repeated."""
        decoder = ProtoRowDecoder(_all_types_descriptor())

        # Hand-encode a complete message. Wire type reference:
        #   0 = varint (int64, bool)
        #   1 = 64-bit (double)
        #   2 = length-delimited (string, bytes, packed repeated)
        #   5 = 32-bit (float)
        wire = bytearray()
        # i64=42
        wire.extend([(1 << 3) | 0, 42])
        # s="hi"
        wire.extend([(2 << 3) | 2, 2, ord("h"), ord("i")])
        # d=1.5 (IEEE 754 double = 0x3FF8000000000000)
        import struct

        wire.extend([(3 << 3) | 1])
        wire.extend(struct.pack("<d", 1.5))
        # b=True
        wire.extend([(4 << 3) | 0, 1])
        # by=b"abc"
        wire.extend([(5 << 3) | 2, 3, ord("a"), ord("b"), ord("c")])
        # reps=[7, 8, 9] encoded as three separate varint entries
        wire.extend([(6 << 3) | 0, 7])
        wire.extend([(6 << 3) | 0, 8])
        wire.extend([(6 << 3) | 0, 9])
        # f=2.5 (IEEE 754 float = 0x40200000)
        wire.extend([(7 << 3) | 5])
        wire.extend(struct.pack("<f", 2.5))

        row = decoder.decode(bytes(wire))
        assert row["i64"] == 42
        assert row["s"] == "hi"
        assert row["d"] == 1.5
        assert row["b"] is True
        assert row["by"] == b"abc"
        assert row["reps"] == [7, 8, 9]
        assert row["f"] == pytest.approx(2.5)


def _nested_descriptor() -> descriptor_pb2.DescriptorProto:
    """Outer message with a nested ``point {x, y}`` submessage."""
    point = descriptor_pb2.DescriptorProto()
    point.name = "Point"
    fx = point.field.add()
    fx.name = "x"
    fx.number = 1
    fx.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    fx.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fy = point.field.add()
    fy.name = "y"
    fy.number = 2
    fy.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    fy.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL

    outer = descriptor_pb2.DescriptorProto()
    outer.name = "Outer"
    outer.nested_type.add().CopyFrom(point)
    fp = outer.field.add()
    fp.name = "p"
    fp.number = 1
    fp.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    fp.type_name = "Outer.Point"
    fp.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    return outer


class TestNestedMessages:
    def test_decodes_nested_submessage(self) -> None:
        """A nested submessage is recursively decoded into a dict."""
        decoder = ProtoRowDecoder(_nested_descriptor())
        # Build the inner message bytes first.
        inner = bytes([(1 << 3) | 0, 3, (2 << 3) | 0, 4])  # x=3, y=4
        wire = bytes([(1 << 3) | 2, len(inner)]) + inner
        row = decoder.decode(wire)
        assert row == {"p": {"x": 3, "y": 4}}

    def test_unset_submessage_is_none(self) -> None:
        """An unset submessage field decodes to ``None``."""
        decoder = ProtoRowDecoder(_nested_descriptor())
        row = decoder.decode(b"")
        assert row == {"p": None}


class TestProtoRowsToArrowTable:
    def test_converts_rows_to_arrow_table(self) -> None:
        """Decoded rows land in an Arrow table matching the target schema."""
        decoder = ProtoRowDecoder(_row_descriptor())
        schema = pa.schema(
            [pa.field("id", pa.int64()), pa.field("name", pa.string())],
        )
        serialized = [_encode_row(1, "a"), _encode_row(2, "b")]
        table = proto_rows_to_arrow_table(decoder, serialized, schema)
        assert table.num_rows == 2
        assert table.column("id").to_pylist() == [1, 2]
        assert table.column("name").to_pylist() == ["a", "b"]

    def test_missing_columns_become_null(self) -> None:
        """Columns in the target schema but not in the writer become NULL."""
        decoder = ProtoRowDecoder(_row_descriptor())
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.string()),
                pa.field("age", pa.int64()),
            ]
        )
        table = proto_rows_to_arrow_table(
            decoder,
            [_encode_row(1, "x")],
            schema,
        )
        assert table.column("age").to_pylist() == [None]

    def test_extra_writer_columns_are_ignored(self) -> None:
        """Writer fields outside the target schema are silently dropped."""
        decoder = ProtoRowDecoder(_row_descriptor())
        # Target schema has only 'id'.
        schema = pa.schema([pa.field("id", pa.int64())])
        table = proto_rows_to_arrow_table(
            decoder,
            [_encode_row(5, "unused")],
            schema,
        )
        assert table.num_rows == 1
        assert table.column_names == ["id"]
