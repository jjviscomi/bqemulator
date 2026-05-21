"""Integration tests: Storage Write API.

Every test uses the bqemu_server fixture (live REST + gRPC in-process)
and the real ``google-cloud-bigquery-storage`` proto-plus types to
exercise the gRPC wire protocol end-to-end.

The matrix covers:

* CreateWriteStream / GetWriteStream / FinalizeWriteStream / FlushRows /
  BatchCommitWriteStreams / AppendRows (bidi) happy paths.
* All four stream types x both input formats (Arrow, protobuf).
* Duplicate-offset → ALREADY_EXISTS; offset-gap → OUT_OF_RANGE.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
from typing import Any

from google.protobuf import descriptor_pb2
import grpc
import pyarrow as pa
import pyarrow.ipc
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _bq_client(bqemu_server: EmulatorServer) -> Any:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="write-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup_test_table(bqemu_server: EmulatorServer, dataset: str = "write_ds") -> None:
    from google.cloud import bigquery

    client = _bq_client(bqemu_server)
    try:
        client.get_dataset(dataset)
    except Exception:  # noqa: BLE001
        client.create_dataset(dataset)
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING"),
    ]
    try:
        client.get_table(f"write-project.{dataset}.target")
    except Exception:  # noqa: BLE001
        client.create_table(
            bigquery.Table(f"write-project.{dataset}.target", schema=schema),
        )


def _read_all_rows(bqemu_server: EmulatorServer, dataset: str = "write_ds") -> list[dict]:
    """Read every row in the target table via the REST tabledata.list API."""
    import requests

    url = (
        f"{bqemu_server.rest_url}/bigquery/v2/projects/write-project/"
        f"datasets/{dataset}/tables/target/data?maxResults=1000"
    )
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _cleanup(bqemu_server: EmulatorServer, dataset: str = "write_ds") -> None:
    try:
        _bq_client(bqemu_server).delete_dataset(
            dataset,
            delete_contents=True,
            not_found_ok=True,
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Helpers for building Arrow / proto payloads
# ---------------------------------------------------------------------------


def _make_arrow_payload(ids: list[int], names: list[str]) -> tuple[bytes, bytes]:
    """Return (writer_schema_bytes, serialized_record_batch) for the given rows."""
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "name": pa.array(names, type=pa.string()),
        }
    )

    # writer_schema: an IPC stream containing only the schema.
    schema_sink = io.BytesIO()
    writer = pa.ipc.new_stream(schema_sink, table.schema)
    writer.close()
    schema_bytes = schema_sink.getvalue()[:-8]  # strip EOS

    # serialized_record_batch: one IPC-serialised batch (without schema header).
    full_sink = io.BytesIO()
    writer = pa.ipc.new_stream(full_sink, table.schema)
    for batch in table.to_batches():
        writer.write_batch(batch)
    writer.close()
    full_bytes = full_sink.getvalue()
    return schema_bytes, full_bytes[len(schema_bytes) :]


def _make_proto_descriptor() -> descriptor_pb2.DescriptorProto:
    """DescriptorProto for {id: int64, name: string} rows."""
    msg = descriptor_pb2.DescriptorProto()
    msg.name = "WriteRow"
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


def _make_proto_rows(ids: list[int], names: list[str]) -> list[bytes]:
    """Build hand-crafted proto wire bytes for each (id, name) pair."""

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

    rows = []
    for id_, name in zip(ids, names, strict=True):
        rows.append(varint((1 << 3) | 0, id_) + string_field((2 << 3) | 2, name))
    return rows


# ---------------------------------------------------------------------------
# gRPC helpers
# ---------------------------------------------------------------------------


_WRITE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"


def _unary(channel: grpc.Channel, method: str, request: Any) -> Any:

    serialized = _REQ_CLASSES[method].serialize(request)
    resp_bytes = channel.unary_unary(f"{_WRITE}/{method}")(serialized)
    return _RESP_CLASSES[method].deserialize(resp_bytes)


def _append_rows(
    channel: grpc.Channel,
    requests: list[Any],
) -> list[Any]:
    from google.cloud.bigquery_storage_v1 import types

    def request_iterator() -> Iterator[bytes]:
        for r in requests:
            yield types.AppendRowsRequest.serialize(r)

    return [
        types.AppendRowsResponse.deserialize(resp_bytes)
        for resp_bytes in channel.stream_stream(f"{_WRITE}/AppendRows")(request_iterator())
    ]


def _req_classes() -> dict[str, Any]:
    from google.cloud.bigquery_storage_v1 import types

    return {
        "CreateWriteStream": types.CreateWriteStreamRequest,
        "GetWriteStream": types.GetWriteStreamRequest,
        "FinalizeWriteStream": types.FinalizeWriteStreamRequest,
        "BatchCommitWriteStreams": types.BatchCommitWriteStreamsRequest,
        "FlushRows": types.FlushRowsRequest,
    }


def _resp_classes() -> dict[str, Any]:
    from google.cloud.bigquery_storage_v1 import types

    return {
        "CreateWriteStream": types.WriteStream,
        "GetWriteStream": types.WriteStream,
        "FinalizeWriteStream": types.FinalizeWriteStreamResponse,
        "BatchCommitWriteStreams": types.BatchCommitWriteStreamsResponse,
        "FlushRows": types.FlushRowsResponse,
    }


_REQ_CLASSES = _req_classes()
_RESP_CLASSES = _resp_classes()


def _build_arrow_request(
    stream_name: str,
    schema_bytes: bytes,
    batch_bytes: bytes,
    offset: int | None = None,
    include_schema: bool = True,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    arrow_rows = types.AppendRowsRequest.ArrowData(
        rows=types.ArrowRecordBatch(
            serialized_record_batch=batch_bytes,
        ),
    )
    if include_schema:
        arrow_rows.writer_schema = types.ArrowSchema(serialized_schema=schema_bytes)
    kwargs: dict[str, Any] = {
        "write_stream": stream_name,
        "arrow_rows": arrow_rows,
    }
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


def _build_proto_request(
    stream_name: str,
    descriptor: descriptor_pb2.DescriptorProto,
    rows: list[bytes],
    offset: int | None = None,
    include_schema: bool = True,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    proto_data = types.AppendRowsRequest.ProtoData(
        rows=types.ProtoRows(serialized_rows=rows),
    )
    if include_schema:
        proto_data.writer_schema = types.ProtoSchema(proto_descriptor=descriptor)
    kwargs: dict[str, Any] = {
        "write_stream": stream_name,
        "proto_rows": proto_data,
    }
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


# ---------------------------------------------------------------------------
# CreateWriteStream / GetWriteStream
# ---------------------------------------------------------------------------


class TestCreateAndGetStream:
    def test_create_write_stream_returns_named_stream(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """CreateWriteStream returns a stream name under the requested table."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            response = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
                ),
            )
            assert response.name.startswith(
                "projects/write-project/datasets/write_ds/tables/target/streams/",
            )
            assert response.type_ == types.WriteStream.Type.COMMITTED
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_get_write_stream_returns_metadata(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """GetWriteStream returns metadata for a previously created stream."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            created = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.PENDING),
                ),
            )
            fetched = _unary(
                channel,
                "GetWriteStream",
                types.GetWriteStreamRequest(name=created.name),
            )
            assert fetched.name == created.name
            assert fetched.type_ == types.WriteStream.Type.PENDING
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# DEFAULT stream
# ---------------------------------------------------------------------------


class TestDefaultStream:
    def test_default_stream_commits_arrow_rows(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Appending Arrow rows to the DEFAULT stream persists them immediately."""
        _setup_test_table(bqemu_server, dataset="default_arrow_ds")
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _make_arrow_payload([1, 2], ["a", "b"])
            stream = (
                "projects/write-project/datasets/default_arrow_ds/tables/target/streams/_default"
            )
            responses = _append_rows(
                channel,
                [_build_arrow_request(stream, schema_bytes, batch_bytes)],
            )
            assert len(responses) == 1
            assert responses[0].error.code == 0

            rows = _read_all_rows(bqemu_server, dataset="default_arrow_ds")
            assert len(rows) == 2
        finally:
            channel.close()
            _cleanup(bqemu_server, dataset="default_arrow_ds")

    def test_default_stream_commits_proto_rows(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Appending proto rows to the DEFAULT stream persists them."""
        # Use a separate dataset so DEFAULT-stream offset state is fresh.
        _setup_test_table(bqemu_server, dataset="default_proto_ds")
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            descriptor = _make_proto_descriptor()
            rows = _make_proto_rows([10, 20, 30], ["x", "y", "z"])
            stream = (
                "projects/write-project/datasets/default_proto_ds/tables/target/streams/_default"
            )
            responses = _append_rows(
                channel,
                [_build_proto_request(stream, descriptor, rows)],
            )
            assert len(responses) == 1
            # DEFAULT streams are long-lived — assert the response reported OK,
            # not a specific offset value.
            assert responses[0].error.code == 0  # no error

            persisted = _read_all_rows(bqemu_server, dataset="default_proto_ds")
            assert len(persisted) == 3
        finally:
            channel.close()
            _cleanup(bqemu_server, dataset="default_proto_ds")


# ---------------------------------------------------------------------------
# COMMITTED stream
# ---------------------------------------------------------------------------


class TestCommittedStream:
    def test_committed_arrow_roundtrip(self, bqemu_server: EmulatorServer) -> None:
        """CreateWriteStream(COMMITTED) + AppendRows persists rows immediately."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
                ),
            )

            schema_bytes, batch_bytes = _make_arrow_payload([1, 2], ["a", "b"])
            responses = _append_rows(
                channel,
                [
                    _build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0),
                ],
            )
            assert responses[0].append_result.offset == 0
            assert len(_read_all_rows(bqemu_server)) == 2
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_duplicate_offset_returns_already_exists(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Sending an already-committed offset returns ALREADY_EXISTS."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload([1, 2], ["a", "b"])
            responses = _append_rows(
                channel,
                [
                    _build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0),
                    # Retry same page — should collide on offset 0.
                    _build_arrow_request(
                        stream.name,
                        schema_bytes,
                        batch_bytes,
                        offset=0,
                        include_schema=False,
                    ),
                ],
            )
            # First succeeded; second has error.
            assert responses[0].append_result.offset == 0
            assert responses[1].error.code == grpc.StatusCode.ALREADY_EXISTS.value[0]

            # Only the first append's 2 rows landed.
            assert len(_read_all_rows(bqemu_server)) == 2
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_gap_offset_returns_out_of_range(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Sending an offset past next_offset returns OUT_OF_RANGE."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload([1], ["a"])
            responses = _append_rows(
                channel,
                [
                    _build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=5),
                ],
            )
            assert responses[0].error.code == grpc.StatusCode.OUT_OF_RANGE.value[0]
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# PENDING stream
# ---------------------------------------------------------------------------


class TestPendingStream:
    def test_pending_append_then_finalize_then_commit(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """PENDING: rows only visible after Finalize + BatchCommit."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.PENDING),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload([1, 2, 3], ["a", "b", "c"])
            responses = _append_rows(
                channel,
                [_build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )
            assert responses[0].append_result.offset == 0

            # Not yet visible — still buffered.
            assert _read_all_rows(bqemu_server) == []

            # Finalize the stream.
            final_resp = _unary(
                channel,
                "FinalizeWriteStream",
                types.FinalizeWriteStreamRequest(name=stream.name),
            )
            assert final_resp.row_count == 3

            # Still buffered after Finalize.
            assert _read_all_rows(bqemu_server) == []

            # Commit → rows now visible.
            commit_resp = _unary(
                channel,
                "BatchCommitWriteStreams",
                types.BatchCommitWriteStreamsRequest(
                    parent="projects/write-project/datasets/write_ds",
                    write_streams=[stream.name],
                ),
            )
            assert not commit_resp.stream_errors
            assert len(_read_all_rows(bqemu_server)) == 3
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_pending_commit_without_finalize_fails(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """BatchCommit without prior Finalize is rejected with an error."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.PENDING),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload([1], ["a"])
            _append_rows(
                channel,
                [_build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )
            # Skip Finalize and go straight to BatchCommit.
            commit_resp = _unary(
                channel,
                "BatchCommitWriteStreams",
                types.BatchCommitWriteStreamsRequest(
                    parent="projects/write-project/datasets/write_ds",
                    write_streams=[stream.name],
                ),
            )
            assert len(commit_resp.stream_errors) == 1
            assert _read_all_rows(bqemu_server) == []
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_pending_proto_path(self, bqemu_server: EmulatorServer) -> None:
        """PENDING stream accepts proto rows identically to Arrow."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.PENDING),
                ),
            )
            descriptor = _make_proto_descriptor()
            rows = _make_proto_rows([7, 8], ["s", "t"])
            _append_rows(
                channel,
                [_build_proto_request(stream.name, descriptor, rows, offset=0)],
            )
            _unary(
                channel,
                "FinalizeWriteStream",
                types.FinalizeWriteStreamRequest(name=stream.name),
            )
            _unary(
                channel,
                "BatchCommitWriteStreams",
                types.BatchCommitWriteStreamsRequest(
                    parent="projects/write-project/datasets/write_ds",
                    write_streams=[stream.name],
                ),
            )
            assert len(_read_all_rows(bqemu_server)) == 2
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# BUFFERED stream
# ---------------------------------------------------------------------------


class TestBufferedStream:
    def test_buffered_flush_publishes_rows(self, bqemu_server: EmulatorServer) -> None:
        """BUFFERED: FlushRows(offset) makes [0, offset) visible."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.BUFFERED),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload([1, 2, 3], ["a", "b", "c"])
            _append_rows(
                channel,
                [_build_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
            )

            # Not visible until FlushRows.
            assert _read_all_rows(bqemu_server) == []

            flush_resp = _unary(
                channel,
                "FlushRows",
                types.FlushRowsRequest(write_stream=stream.name, offset=2),
            )
            assert flush_resp.offset == 2
            assert len(_read_all_rows(bqemu_server)) == 2

            # A second flush covers the remaining row.
            flush_resp = _unary(
                channel,
                "FlushRows",
                types.FlushRowsRequest(write_stream=stream.name, offset=3),
            )
            assert flush_resp.offset == 3
            assert len(_read_all_rows(bqemu_server)) == 3
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_buffered_proto_rows(self, bqemu_server: EmulatorServer) -> None:
        """BUFFERED accepts proto rows and Flush publishes them."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.BUFFERED),
                ),
            )
            descriptor = _make_proto_descriptor()
            proto_rows = _make_proto_rows([100, 200], ["aa", "bb"])
            _append_rows(
                channel,
                [_build_proto_request(stream.name, descriptor, proto_rows, offset=0)],
            )
            _unary(
                channel,
                "FlushRows",
                types.FlushRowsRequest(write_stream=stream.name, offset=2),
            )
            assert len(_read_all_rows(bqemu_server)) == 2
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# Multiple batches: schema sticks across messages
# ---------------------------------------------------------------------------


class TestMultiBatchStreaming:
    def test_subsequent_append_omits_schema(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Second AppendRows omits writer_schema and reuses the first's."""
        _setup_test_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = _unary(
                channel,
                "CreateWriteStream",
                types.CreateWriteStreamRequest(
                    parent="projects/write-project/datasets/write_ds/tables/target",
                    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
                ),
            )
            schema_bytes, batch1 = _make_arrow_payload([1], ["a"])
            _, batch2 = _make_arrow_payload([2, 3], ["b", "c"])
            responses = _append_rows(
                channel,
                [
                    _build_arrow_request(stream.name, schema_bytes, batch1, offset=0),
                    _build_arrow_request(
                        stream.name,
                        schema_bytes,
                        batch2,
                        offset=1,
                        include_schema=False,
                    ),
                ],
            )
            assert responses[0].append_result.offset == 0
            assert responses[1].append_result.offset == 1
            assert len(_read_all_rows(bqemu_server)) == 3
        finally:
            channel.close()
            _cleanup(bqemu_server)
