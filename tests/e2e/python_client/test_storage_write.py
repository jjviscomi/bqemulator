"""E2E: Phase 5 Storage Write API against a live container.

Exercises every stream type (DEFAULT, COMMITTED, PENDING, BUFFERED)
and both input formats (Arrow IPC, dynamic protobuf) end-to-end over
a real gRPC channel using the proto-plus types shipped by
``google-cloud-bigquery-storage``. This pairs with the ship criterion
declared in ``docs/roadmap/phase-5-storage-write-api.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
from typing import Any

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
from google.protobuf import descriptor_pb2
import grpc
import pyarrow as pa
import pyarrow.ipc
import pytest

pytestmark = pytest.mark.e2e

_WRITE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"


def _bq_client(rest_url: str) -> bigquery.Client:
    return bigquery.Client(
        project="e2e-write",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=rest_url),
    )


def _seed(rest_url: str, dataset: str, table: str = "target") -> None:
    client = _bq_client(rest_url)
    try:
        client.create_dataset(
            bigquery.Dataset(f"{client.project}.{dataset}"),
            exists_ok=True,
        )
        bq_table = bigquery.Table(
            f"{client.project}.{dataset}.{table}",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("name", "STRING"),
            ],
        )
        client.create_table(bq_table, exists_ok=True)
    finally:
        client.close()


def _cleanup(rest_url: str, dataset: str) -> None:
    client = _bq_client(rest_url)
    try:
        client.delete_dataset(dataset, delete_contents=True, not_found_ok=True)
    finally:
        client.close()


def _count_rows(rest_url: str, dataset: str, table: str = "target") -> int:
    import requests

    r = requests.get(
        f"{rest_url}/bigquery/v2/projects/e2e-write/datasets/{dataset}/tables/{table}/data",
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json().get("totalRows", "0"))


def _arrow_payload(ids: list[int], names: list[str]) -> tuple[bytes, bytes]:
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "name": pa.array(names, type=pa.string()),
        }
    )
    schema_sink = io.BytesIO()
    writer = pa.ipc.new_stream(schema_sink, table.schema)
    writer.close()
    schema_bytes = schema_sink.getvalue()[:-8]

    full_sink = io.BytesIO()
    writer = pa.ipc.new_stream(full_sink, table.schema)
    for batch in table.to_batches():
        writer.write_batch(batch)
    writer.close()
    full_bytes = full_sink.getvalue()
    return schema_bytes, full_bytes[len(schema_bytes) :]


def _proto_descriptor() -> descriptor_pb2.DescriptorProto:
    msg = descriptor_pb2.DescriptorProto()
    msg.name = "Row"
    for number, name, type_ in [
        (1, "id", descriptor_pb2.FieldDescriptorProto.TYPE_INT64),
        (2, "name", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
    ]:
        f = msg.field.add()
        f.name = name
        f.number = number
        f.type = type_
        f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    return msg


def _proto_rows(ids: list[int], names: list[str]) -> list[bytes]:
    def varint(tag: int, value: int) -> bytes:
        out = bytearray([tag])
        while True:
            b = value & 0x7F
            value >>= 7
            if value:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
        return bytes(out)

    def string_field(tag: int, value: str) -> bytes:
        data = value.encode("utf-8")
        return bytes([tag, len(data), *data])

    return [
        varint((1 << 3) | 0, i) + string_field((2 << 3) | 2, n)
        for i, n in zip(ids, names, strict=True)
    ]


def _arrow_request(
    stream: str,
    schema_bytes: bytes,
    batch_bytes: bytes,
    offset: int | None = None,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    arrow_rows = types.AppendRowsRequest.ArrowData(
        writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
        rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
    )
    kwargs: dict[str, Any] = {"write_stream": stream, "arrow_rows": arrow_rows}
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


def _proto_request(
    stream: str,
    descriptor: descriptor_pb2.DescriptorProto,
    rows: list[bytes],
    offset: int | None = None,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    proto_data = types.AppendRowsRequest.ProtoData(
        writer_schema=types.ProtoSchema(proto_descriptor=descriptor),
        rows=types.ProtoRows(serialized_rows=rows),
    )
    kwargs: dict[str, Any] = {"write_stream": stream, "proto_rows": proto_data}
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


def _append(channel: grpc.Channel, requests: list[Any]) -> list[Any]:
    from google.cloud.bigquery_storage_v1 import types

    def it() -> Iterator[bytes]:
        for r in requests:
            yield types.AppendRowsRequest.serialize(r)

    return [
        types.AppendRowsResponse.deserialize(b)
        for b in channel.stream_stream(f"{_WRITE}/AppendRows")(it())
    ]


# ---------------------------------------------------------------------------
# DEFAULT x Arrow / proto
# ---------------------------------------------------------------------------


def test_default_stream_arrow_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """DEFAULT stream + Arrow rows make the full round-trip through the live container."""
    ds = "write_default_arrow"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _arrow_payload([1, 2], ["alice", "bob"])
            stream = f"projects/e2e-write/datasets/{ds}/tables/target/streams/_default"
            responses = _append(
                channel,
                [_arrow_request(stream, schema_bytes, batch_bytes)],
            )
            assert responses[0].error.code == 0
            assert _count_rows(bqemu_rest_url, ds) == 2
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


def test_default_stream_proto_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """DEFAULT stream + dynamic protobuf rows persist in the target table."""
    ds = "write_default_proto"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            descriptor = _proto_descriptor()
            rows = _proto_rows([10, 20, 30], ["x", "y", "z"])
            stream = f"projects/e2e-write/datasets/{ds}/tables/target/streams/_default"
            responses = _append(
                channel,
                [_proto_request(stream, descriptor, rows)],
            )
            assert responses[0].error.code == 0
            assert _count_rows(bqemu_rest_url, ds) == 3
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


# ---------------------------------------------------------------------------
# COMMITTED x Arrow / proto, including offset dedup
# ---------------------------------------------------------------------------


def test_committed_stream_arrow_e2e_and_dedup(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """COMMITTED stream commits immediately and rejects duplicate offsets."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_committed_arrow"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream_resp = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            schema_bytes, batch_bytes = _arrow_payload([1, 2], ["a", "b"])
            responses = _append(
                channel,
                [
                    _arrow_request(stream_resp.name, schema_bytes, batch_bytes, offset=0),
                    # Retry same offset -> ALREADY_EXISTS in the response.
                    _arrow_request(stream_resp.name, schema_bytes, batch_bytes, offset=0),
                ],
            )
            assert responses[0].append_result.offset == 0
            assert responses[1].error.code == grpc.StatusCode.ALREADY_EXISTS.value[0]
            assert _count_rows(bqemu_rest_url, ds) == 2
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


def test_committed_stream_proto_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """COMMITTED stream accepts dynamic proto rows end-to-end."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_committed_proto"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream_resp = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            descriptor = _proto_descriptor()
            rows = _proto_rows([1, 2, 3], ["a", "b", "c"])
            responses = _append(
                channel,
                [_proto_request(stream_resp.name, descriptor, rows, offset=0)],
            )
            assert responses[0].append_result.offset == 0
            assert _count_rows(bqemu_rest_url, ds) == 3
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


# ---------------------------------------------------------------------------
# PENDING x Arrow / proto, including BatchCommit gating
# ---------------------------------------------------------------------------


def test_pending_stream_arrow_batchcommit_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """PENDING stream is invisible until Finalize + BatchCommitWriteStreams."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_pending_arrow"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.PENDING,
                            ),
                        ),
                    ),
                ),
            )
            schema_bytes, batch_bytes = _arrow_payload([1, 2, 3], ["a", "b", "c"])
            _append(
                channel,
                [_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )
            # Not visible yet.
            assert _count_rows(bqemu_rest_url, ds) == 0

            channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                types.FinalizeWriteStreamRequest.serialize(
                    types.FinalizeWriteStreamRequest(name=stream.name),
                ),
            )
            resp = types.BatchCommitWriteStreamsResponse.deserialize(
                channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                    types.BatchCommitWriteStreamsRequest.serialize(
                        types.BatchCommitWriteStreamsRequest(
                            parent=f"projects/e2e-write/datasets/{ds}",
                            write_streams=[stream.name],
                        ),
                    ),
                ),
            )
            assert list(resp.stream_errors) == []
            assert _count_rows(bqemu_rest_url, ds) == 3
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


def test_pending_stream_proto_batchcommit_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """PENDING stream + proto rows round-trip through Finalize + BatchCommit."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_pending_proto"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.PENDING,
                            ),
                        ),
                    ),
                ),
            )
            descriptor = _proto_descriptor()
            rows = _proto_rows([9, 8], ["x", "y"])
            _append(channel, [_proto_request(stream.name, descriptor, rows, offset=0)])
            channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                types.FinalizeWriteStreamRequest.serialize(
                    types.FinalizeWriteStreamRequest(name=stream.name),
                ),
            )
            channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                types.BatchCommitWriteStreamsRequest.serialize(
                    types.BatchCommitWriteStreamsRequest(
                        parent=f"projects/e2e-write/datasets/{ds}",
                        write_streams=[stream.name],
                    ),
                ),
            )
            assert _count_rows(bqemu_rest_url, ds) == 2
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


# ---------------------------------------------------------------------------
# BUFFERED x Arrow / proto, with FlushRows
# ---------------------------------------------------------------------------


def test_buffered_stream_arrow_flush_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """BUFFERED stream publishes rows up to FlushRows' offset."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_buffered_arrow"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.BUFFERED,
                            ),
                        ),
                    ),
                ),
            )
            schema_bytes, batch_bytes = _arrow_payload([1, 2, 3], ["a", "b", "c"])
            _append(
                channel,
                [_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )
            assert _count_rows(bqemu_rest_url, ds) == 0

            channel.unary_unary(f"{_WRITE}/FlushRows")(
                types.FlushRowsRequest.serialize(
                    types.FlushRowsRequest(write_stream=stream.name, offset=2),
                ),
            )
            assert _count_rows(bqemu_rest_url, ds) == 2

            channel.unary_unary(f"{_WRITE}/FlushRows")(
                types.FlushRowsRequest.serialize(
                    types.FlushRowsRequest(write_stream=stream.name, offset=3),
                ),
            )
            assert _count_rows(bqemu_rest_url, ds) == 3
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


def test_buffered_stream_proto_flush_e2e(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """BUFFERED + proto rows published via FlushRows."""
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_buffered_proto"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.BUFFERED,
                            ),
                        ),
                    ),
                ),
            )
            descriptor = _proto_descriptor()
            rows = _proto_rows([1, 2], ["a", "b"])
            _append(channel, [_proto_request(stream.name, descriptor, rows, offset=0)])
            channel.unary_unary(f"{_WRITE}/FlushRows")(
                types.FlushRowsRequest.serialize(
                    types.FlushRowsRequest(write_stream=stream.name, offset=2),
                ),
            )
            assert _count_rows(bqemu_rest_url, ds) == 2
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)


# ---------------------------------------------------------------------------
# Server-side safety: reject oversized payloads
# ---------------------------------------------------------------------------


def test_oversized_append_is_rejected_by_container(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """The container's 10 MiB cap rejects oversized AppendRows payloads.

    We send a payload between the app cap (10 MiB) and the transport
    cap (10 MiB + 1 MiB headroom) so the *app-level* size check fires
    — the client sees ``AppendRowsResponse.error.code = RESOURCE_EXHAUSTED``
    rather than a transport-level rejection. Either path returns
    RESOURCE_EXHAUSTED, so both are acceptable; we check for the app
    path because it's the one the strategy guard covers.
    """
    from google.cloud.bigquery_storage_v1 import types

    ds = "write_oversize"
    _seed(bqemu_rest_url, ds)
    try:
        channel = grpc.insecure_channel(
            bqemu_grpc_endpoint,
            options=[
                ("grpc.max_send_message_length", 64 * 1024 * 1024),
                ("grpc.max_receive_message_length", 64 * 1024 * 1024),
            ],
        )
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent=f"projects/e2e-write/datasets/{ds}/tables/target",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            # ~10.5 MiB payload: 180k rows x ~60 bytes each. Sits between
            # the 10 MiB app cap and the ~11 MiB transport cap.
            schema_bytes, batch_bytes = _arrow_payload(
                list(range(180_000)),
                ["x" * 50] * 180_000,
            )
            responses = _append(
                channel,
                [_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )
            assert responses[0].error.code == grpc.StatusCode.RESOURCE_EXHAUSTED.value[0]
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url, ds)
