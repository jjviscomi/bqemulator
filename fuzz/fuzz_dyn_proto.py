r"""Atheris harness for the Storage Write API dynamic-protobuf decoder.

The Storage Write API servicer accepts a writer ``DescriptorProto`` on
the first ``AppendRowsRequest`` of a connection and uses it to decode
every subsequent row's wire bytes via
:meth:`bqemulator.streaming.proto_deserializer.ProtoRowDecoder.decode`.
The decoder is therefore a primary attack surface for malformed wire
bytes — a client that ships oversize tags, mis-typed fields, or
truncated length prefixes must surface a clean error envelope rather
than crashing the servicer process.

Contract:

* :class:`google.protobuf.message.DecodeError` is the documented proto
  error class; the wire-level parser raises it on any malformed input.
  The harness catches it because the upstream servicer is expected to
  map it to a :class:`bqemulator.domain.errors.InvalidQueryError` on
  the way out — but the *decoder itself* doing so would require a
  catch in the hot path the v1.0 contract does not require.
* :class:`bqemulator.domain.errors.DomainError` is caught for the same
  forward-compat reason as in
  :mod:`fuzz_sql_translator` — a future hardening pass may rewrite the
  decoder to surface domain errors directly.
* Any other exception (``TypeError``, ``IndexError``,
  ``UnicodeDecodeError``, ``ValueError`` from inner pyarrow calls,
  etc.) escaping ``decode`` is a bug — these are exactly the crash
  classes the resume-prompt enumerates as the fuzz target.

The harness uses a fixed writer schema (one of every common BigQuery
column type) so coverage-guided mutation can explore the proto wire-
format surface against a single representative target shape.

Run directly::

    python fuzz/fuzz_dyn_proto.py -max_total_time=60 \
        fuzz/corpus/dyn_proto
"""

from __future__ import annotations

import sys

import atheris

with atheris.instrument_imports():
    from bqemulator.streaming.proto_deserializer import ProtoRowDecoder

from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import DecodeError

from bqemulator.domain.errors import DomainError


def _build_fixed_descriptor() -> descriptor_pb2.DescriptorProto:
    """Build a representative writer schema used as the decoder target.

    One field per major BigQuery → proto type mapping so the fuzzer's
    coverage signal exercises the breadth of
    :func:`bqemulator.streaming.proto_deserializer._decode_field_value`
    branches without per-iteration setup. proto2-style optional fields
    on the boundary types give the presence-tracking branches their
    fuzz signal too.
    """
    descriptor = descriptor_pb2.DescriptorProto()
    descriptor.name = "FuzzRow"

    def _add_field(name: str, number: int, ftype: int, *, label: int | None = None) -> None:
        field = descriptor.field.add()
        field.name = name
        field.number = number
        field.type = ftype
        field.label = label if label is not None else FieldDescriptor.LABEL_OPTIONAL

    _add_field("int_col", 1, FieldDescriptor.TYPE_INT64)
    _add_field("double_col", 2, FieldDescriptor.TYPE_DOUBLE)
    _add_field("string_col", 3, FieldDescriptor.TYPE_STRING)
    _add_field("bytes_col", 4, FieldDescriptor.TYPE_BYTES)
    _add_field("bool_col", 5, FieldDescriptor.TYPE_BOOL)
    _add_field(
        "repeated_int_col",
        6,
        FieldDescriptor.TYPE_INT64,
        label=FieldDescriptor.LABEL_REPEATED,
    )
    _add_field(
        "repeated_string_col",
        7,
        FieldDescriptor.TYPE_STRING,
        label=FieldDescriptor.LABEL_REPEATED,
    )
    return descriptor


_DECODER = ProtoRowDecoder(_build_fixed_descriptor())


def TestOneInput(data: bytes) -> None:  # noqa: N802 — libFuzzer entry-point name
    """LibFuzzer entry point — decode one fuzzer-provided wire payload."""
    try:
        _DECODER.decode(data)
    except DecodeError:
        return
    except DomainError:
        return


def _main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    _main()
