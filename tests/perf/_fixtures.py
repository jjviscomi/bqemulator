"""Shared dataset + payload builders for the perf tier.

Every perf scenario constructs the same fixed dataset before the timed
block starts; this module owns the construction so the benchmarks read
as wall-clock-cost only. Per [`ADR 0025 §5`](../../docs/adr/0025-perf-tier-design-contract.md)
data is built outside the benchmark callable.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.ipc

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.server import EmulatorServer


# Default row counts for the throughput scenarios. The 100 K row table
# is large enough for the per-round time to dominate Python-loop
# overhead but small enough that 5+ rounds finish inside a 60 s budget.
DEFAULT_THROUGHPUT_ROWS = 100_000


def make_test_dataset(
    bqemu_server: EmulatorServer,
    project: str,
    dataset: str,
    table: str,
    row_count: int = DEFAULT_THROUGHPUT_ROWS,
) -> str:
    """Create a dataset + table with ``row_count`` rows of synthetic data.

    Returns the fully-qualified ``project.dataset.table`` string. The
    schema is fixed to ``(id INT64, value STRING)`` so every perf
    scenario reads the same shape.

    Implementation note: this is *not* timed — it runs once at
    benchmark setup. The 100 K rows are inserted via the in-process
    storage engine directly (not REST insertAll) so setup cost stays
    bounded.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    try:
        client.get_dataset(dataset)
    except Exception:  # noqa: BLE001 — bigquery client surface
        client.create_dataset(dataset)

    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("value", "STRING"),
    ]
    table_ref = f"{project}.{dataset}.{table}"
    try:
        client.get_table(table_ref)
        # Table already populated — drop and recreate to keep row_count exact.
        client.delete_table(table_ref)
    except Exception:  # noqa: BLE001 — bigquery client surface
        pass

    client.create_table(bigquery.Table(table_ref, schema=schema))

    # Insert via the official insert_rows_json — slower than direct
    # engine insertion but lets us reuse a single fixture for both REST
    # and Storage-Read scenarios without setup-path divergence.
    chunk = 5000
    for start in range(0, row_count, chunk):
        rows = [
            {"id": i, "value": f"row_{i:08d}"} for i in range(start, min(start + chunk, row_count))
        ]
        errors = client.insert_rows_json(table_ref, rows)
        if errors:  # pragma: no cover — fixture failure shouldn't happen
            msg = f"insert_rows_json returned errors during perf-fixture build: {errors}"
            raise RuntimeError(msg)

    return table_ref


def make_arrow_payload(ids: list[int], values: list[str]) -> tuple[bytes, bytes]:
    """Build (writer_schema_bytes, serialized_record_batch) for a (id, value) row set.

    Mirrors the helper at `tests/integration/test_storage_write_api.py::_make_arrow_payload`
    so the Storage-Write benchmark uses the same wire shape every other
    Arrow path in the codebase uses.
    """
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "value": pa.array(values, type=pa.string()),
        }
    )

    schema_sink = io.BytesIO()
    writer = pa.ipc.new_stream(schema_sink, table.schema)
    writer.close()
    schema_bytes = schema_sink.getvalue()[:-8]  # strip EOS

    full_sink = io.BytesIO()
    writer = pa.ipc.new_stream(full_sink, table.schema)
    for batch in table.to_batches():
        writer.write_batch(batch)
    writer.close()
    full_bytes = full_sink.getvalue()
    return schema_bytes, full_bytes[len(schema_bytes) :]


def make_proto_descriptor() -> Any:
    """DescriptorProto for ``{id INT64, value STRING}`` rows."""
    from google.protobuf import descriptor_pb2

    msg = descriptor_pb2.DescriptorProto()
    msg.name = "PerfRow"
    f1 = msg.field.add()
    f1.name = "id"
    f1.number = 1
    f1.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    f1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f2 = msg.field.add()
    f2.name = "value"
    f2.number = 2
    f2.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    f2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    return msg


def make_proto_rows(ids: list[int], values: list[str]) -> list[bytes]:
    """Hand-encode proto wire bytes for a list of (id, value) pairs."""

    def varint(tag: int, value: int) -> bytes:
        out = bytearray([tag])
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
        return bytes(out)

    def string_field(tag: int, value: str) -> bytes:
        data = value.encode("utf-8")
        return bytes([tag, len(data), *data])

    rows: list[bytes] = []
    for id_, value in zip(ids, values, strict=True):
        rows.append(varint((1 << 3) | 0, id_) + string_field((2 << 3) | 2, value))
    return rows
