"""Integration tests: Storage Write API error paths and edge cases.

These tests drive each error branch in the BigQueryWrite servicer so the
Phase 5 coverage audit can point at green numbers for every path. They
complement the happy-path tests in ``test_storage_write_api.py``.
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


_WRITE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"


def _bq_client(bqemu_server: EmulatorServer) -> Any:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="write-edge",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup_edge_table(bqemu_server: EmulatorServer) -> None:
    from google.cloud import bigquery

    client = _bq_client(bqemu_server)
    try:
        client.get_dataset("edge_ds")
    except Exception:  # noqa: BLE001
        client.create_dataset("edge_ds")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING"),
    ]
    try:
        client.get_table("write-edge.edge_ds.tbl")
    except Exception:  # noqa: BLE001
        client.create_table(
            bigquery.Table("write-edge.edge_ds.tbl", schema=schema),
        )


def _cleanup(bqemu_server: EmulatorServer) -> None:
    try:
        _bq_client(bqemu_server).delete_dataset(
            "edge_ds",
            delete_contents=True,
            not_found_ok=True,
        )
    except Exception:  # noqa: BLE001
        pass


def _make_arrow_payload() -> tuple[bytes, bytes]:
    table = pa.table(
        {
            "id": pa.array([1], type=pa.int64()),
            "name": pa.array(["a"], type=pa.string()),
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


def _make_descriptor() -> descriptor_pb2.DescriptorProto:
    msg = descriptor_pb2.DescriptorProto()
    msg.name = "Row"
    f = msg.field.add()
    f.name = "id"
    f.number = 1
    f.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    return msg


def _append_rows(channel: grpc.Channel, requests: list[Any]) -> list[Any]:
    from google.cloud.bigquery_storage_v1 import types

    def it() -> Iterator[bytes]:
        for r in requests:
            yield types.AppendRowsRequest.serialize(r)

    return [
        types.AppendRowsResponse.deserialize(b)
        for b in channel.stream_stream(f"{_WRITE}/AppendRows")(it())
    ]


# ---------------------------------------------------------------------------
# CreateWriteStream error paths
# ---------------------------------------------------------------------------


class TestCreateWriteStreamErrors:
    def test_invalid_parent_returns_invalid_argument(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """A garbled parent returns INVALID_ARGUMENT."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.CreateWriteStreamRequest(
                parent="garbage/parent",
                write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()

    def test_unknown_table_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """CreateWriteStream against a nonexistent table returns NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.CreateWriteStreamRequest(
                parent="projects/ghost/datasets/ghost/tables/ghost",
                write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_default_type_is_rejected(self, bqemu_server: EmulatorServer) -> None:
        """TYPE_UNSPECIFIED / DEFAULT cannot be explicitly created."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.CreateWriteStreamRequest(
                parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                write_stream=types.WriteStream(
                    type_=types.WriteStream.Type.TYPE_UNSPECIFIED,
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# GetWriteStream error paths
# ---------------------------------------------------------------------------


class TestGetWriteStreamErrors:
    def test_unknown_stream_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """GetWriteStream on an unregistered (non-default) stream 404s."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            # Use a canonical-shape stream id (16 hex chars) that
            # simply doesn't exist. Real BigQuery distinguishes
            # malformed ids (INVALID_ARGUMENT) from unknown valid ids
            # (NOT_FOUND); the wire-format conformance suite (P3.d)
            # asserts that contract.
            request = types.GetWriteStreamRequest(
                name="projects/x/datasets/y/tables/z/streams/deadbeefcafebabe",
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                    types.GetWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_malformed_stream_name_returns_invalid_argument(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """GetWriteStream on a malformed (non-canonical) stream id 400s."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.GetWriteStreamRequest(
                name="projects/x/datasets/y/tables/z/streams/ghost",
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                    types.GetWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()

    def test_default_stream_for_unknown_table_is_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """GetWriteStream on the default stream of a missing table 404s."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.GetWriteStreamRequest(
                name="projects/x/datasets/y/tables/z/streams/_default",
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                    types.GetWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_default_stream_for_existing_table_is_auto_created(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Fetching the default stream of a real table auto-creates it."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.GetWriteStreamRequest(
                name="projects/write-edge/datasets/edge_ds/tables/tbl/streams/_default",
            )
            resp = channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                types.GetWriteStreamRequest.serialize(request),
            )
            parsed = types.WriteStream.deserialize(resp)
            assert parsed.name.endswith("/streams/_default")
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_malformed_default_stream_name_is_invalid(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """A badly-shaped default stream name returns INVALID_ARGUMENT."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.GetWriteStreamRequest(name="garbage/streams/_default")
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                    types.GetWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()


# ---------------------------------------------------------------------------
# FinalizeWriteStream error paths
# ---------------------------------------------------------------------------


class TestFinalizeErrors:
    def test_finalize_unknown_stream_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Finalizing a stream that was never created returns NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.FinalizeWriteStreamRequest(
                name="projects/x/datasets/y/tables/z/streams/ghost",
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(request),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_finalize_default_stream_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Finalize on the implicit DEFAULT stream returns INVALID_ARGUMENT."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            # Instantiate the DEFAULT stream first via Get.
            channel.unary_unary(f"{_WRITE}/GetWriteStream")(
                types.GetWriteStreamRequest.serialize(
                    types.GetWriteStreamRequest(
                        name=("projects/write-edge/datasets/edge_ds/tables/tbl/streams/_default"),
                    ),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(
                        types.FinalizeWriteStreamRequest(
                            name=(
                                "projects/write-edge/datasets/edge_ds/tables/tbl/streams/_default"
                            ),
                        ),
                    ),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_finalize_is_idempotent(self, bqemu_server: EmulatorServer) -> None:
        """Calling Finalize twice on the same stream returns the same row_count.

        This is an audit finding from Phase 5: without the idempotency check
        the active-stream gauge was double-decremented on retries.
        """
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            first = types.FinalizeWriteStreamResponse.deserialize(
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(
                        types.FinalizeWriteStreamRequest(name=stream.name),
                    ),
                ),
            )
            second = types.FinalizeWriteStreamResponse.deserialize(
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(
                        types.FinalizeWriteStreamRequest(name=stream.name),
                    ),
                ),
            )
            assert first.row_count == second.row_count
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_finalize_committed_stream_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Finalizing an already-committed PENDING stream is rejected."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.PENDING,
                            ),
                        ),
                    ),
                ),
            )
            # Finalize then commit so the stream reaches COMMITTED state.
            channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                types.FinalizeWriteStreamRequest.serialize(
                    types.FinalizeWriteStreamRequest(name=stream.name),
                ),
            )
            channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                types.BatchCommitWriteStreamsRequest.serialize(
                    types.BatchCommitWriteStreamsRequest(
                        parent="projects/write-edge/datasets/edge_ds",
                        write_streams=[stream.name],
                    ),
                ),
            )
            # Now Finalize should be a FAILED_PRECONDITION.
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(
                        types.FinalizeWriteStreamRequest(name=stream.name),
                    ),
                )
            assert exc.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# BatchCommit error paths
# ---------------------------------------------------------------------------


class TestBatchCommitErrors:
    def test_batch_commit_unknown_stream_returns_error(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Unknown streams appear in ``stream_errors`` with STREAM_NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            resp_bytes = channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                types.BatchCommitWriteStreamsRequest.serialize(
                    types.BatchCommitWriteStreamsRequest(
                        parent="projects/x/datasets/y",
                        write_streams=["projects/x/datasets/y/tables/z/streams/ghost"],
                    ),
                ),
            )
            resp = types.BatchCommitWriteStreamsResponse.deserialize(resp_bytes)
            assert len(resp.stream_errors) == 1
            assert (
                resp.stream_errors[0].code == types.StorageError.StorageErrorCode.STREAM_NOT_FOUND
            )
        finally:
            channel.close()

    def test_batch_commit_not_finalized_stream_is_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """PENDING stream that hasn't been finalized is rejected.

        This is an audit finding from Phase 5: the commit path previously
        mutated stream state before validating the batch, leaking half-
        committed state when a later stream in the batch errored.
        """
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.PENDING,
                            ),
                        ),
                    ),
                ),
            )
            # Skip Finalize — BatchCommit should refuse.
            resp_bytes = channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                types.BatchCommitWriteStreamsRequest.serialize(
                    types.BatchCommitWriteStreamsRequest(
                        parent="projects/write-edge/datasets/edge_ds",
                        write_streams=[stream.name],
                    ),
                ),
            )
            resp = types.BatchCommitWriteStreamsResponse.deserialize(resp_bytes)
            assert len(resp.stream_errors) == 1
            assert (
                resp.stream_errors[0].code
                == types.StorageError.StorageErrorCode.INVALID_STREAM_STATE
            )
            # The PENDING stream must still be in its original OPEN state so
            # a subsequent Finalize+BatchCommit can succeed cleanly.
            final_resp = types.FinalizeWriteStreamResponse.deserialize(
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(
                        types.FinalizeWriteStreamRequest(name=stream.name),
                    ),
                ),
            )
            assert final_resp.row_count == 0
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_batch_commit_non_pending_stream_is_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """A COMMITTED stream cannot be batch-committed; returns INVALID_STREAM_TYPE."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            resp_bytes = channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                types.BatchCommitWriteStreamsRequest.serialize(
                    types.BatchCommitWriteStreamsRequest(
                        parent="projects/write-edge/datasets/edge_ds",
                        write_streams=[stream.name],
                    ),
                ),
            )
            resp = types.BatchCommitWriteStreamsResponse.deserialize(resp_bytes)
            assert len(resp.stream_errors) == 1
            assert (
                resp.stream_errors[0].code
                == types.StorageError.StorageErrorCode.INVALID_STREAM_TYPE
            )
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# FlushRows error paths
# ---------------------------------------------------------------------------


class TestFlushRowsErrors:
    def test_flush_unknown_stream_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """FlushRows on a nonexistent stream returns NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            req = types.FlushRowsRequest(
                write_stream="projects/x/datasets/y/tables/z/streams/ghost",
                offset=1,
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FlushRows")(
                    types.FlushRowsRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_flush_non_buffered_stream_is_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """FlushRows on a COMMITTED stream returns INVALID_ARGUMENT."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            req = types.FlushRowsRequest(write_stream=stream.name, offset=1)
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FlushRows")(
                    types.FlushRowsRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_flush_bad_offset_returns_out_of_range(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """FlushRows past the buffered frontier returns OUT_OF_RANGE."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.BUFFERED,
                            ),
                        ),
                    ),
                ),
            )
            req = types.FlushRowsRequest(write_stream=stream.name, offset=10)
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FlushRows")(
                    types.FlushRowsRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.OUT_OF_RANGE
        finally:
            channel.close()
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# AppendRows error paths
# ---------------------------------------------------------------------------


class TestAppendRowsErrors:
    def test_append_rows_unknown_stream_closes_with_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """AppendRows against a never-created non-default stream 404s."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _make_arrow_payload()
            request = types.AppendRowsRequest(
                write_stream="projects/x/datasets/y/tables/z/streams/ghost",
                arrow_rows=types.AppendRowsRequest.ArrowData(
                    writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
                    rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                list(
                    channel.stream_stream(f"{_WRITE}/AppendRows")(
                        iter([types.AppendRowsRequest.serialize(request)]),
                    ),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_append_default_stream_missing_table_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Default stream on a missing table closes the RPC with NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _make_arrow_payload()
            request = types.AppendRowsRequest(
                write_stream="projects/x/datasets/y/tables/z/streams/_default",
                arrow_rows=types.AppendRowsRequest.ArrowData(
                    writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
                    rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                list(
                    channel.stream_stream(f"{_WRITE}/AppendRows")(
                        iter([types.AppendRowsRequest.serialize(request)]),
                    ),
                )
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            channel.close()

    def test_append_bad_default_stream_name(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """A malformed default-stream name returns INVALID_ARGUMENT."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _make_arrow_payload()
            request = types.AppendRowsRequest(
                write_stream="garbage/streams/_default",
                arrow_rows=types.AppendRowsRequest.ArrowData(
                    writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
                    rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                list(
                    channel.stream_stream(f"{_WRITE}/AppendRows")(
                        iter([types.AppendRowsRequest.serialize(request)]),
                    ),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()

    def test_append_proto_without_schema_errors(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Proto payload without a prior writer_schema yields an error response."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            # ProtoData but no writer_schema — expect an error in the response.
            proto_data = types.AppendRowsRequest.ProtoData(
                rows=types.ProtoRows(serialized_rows=[b"\x08\x01"]),
            )
            request = types.AppendRowsRequest(
                write_stream=stream.name,
                proto_rows=proto_data,
            )
            responses = _append_rows(channel, [request])
            assert len(responses) == 1
            assert responses[0].error.code != 0
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_append_empty_request_is_accepted(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """An AppendRows with no proto/arrow data is accepted (keep-alive)."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            request = types.AppendRowsRequest(write_stream=stream.name)
            responses = _append_rows(channel, [request])
            assert len(responses) == 1
            assert responses[0].error.code == 0  # no error
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_append_on_finalized_committed_stream_errors(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Appends against a finalized COMMITTED stream return an error response."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.COMMITTED,
                            ),
                        ),
                    ),
                ),
            )
            channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                types.FinalizeWriteStreamRequest.serialize(
                    types.FinalizeWriteStreamRequest(name=stream.name),
                ),
            )
            schema_bytes, batch_bytes = _make_arrow_payload()
            request = types.AppendRowsRequest(
                write_stream=stream.name,
                arrow_rows=types.AppendRowsRequest.ArrowData(
                    writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
                    rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
                ),
                offset=0,
            )
            responses = _append_rows(channel, [request])
            assert responses[0].error.code == (grpc.StatusCode.FAILED_PRECONDITION.value[0])
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_append_default_stream_with_offset_is_rejected(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """DEFAULT streams don't accept offsets — error returned."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            schema_bytes, batch_bytes = _make_arrow_payload()
            request = types.AppendRowsRequest(
                write_stream=("projects/write-edge/datasets/edge_ds/tables/tbl/streams/_default"),
                arrow_rows=types.AppendRowsRequest.ArrowData(
                    writer_schema=types.ArrowSchema(serialized_schema=schema_bytes),
                    rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
                ),
                offset=0,
            )
            responses = _append_rows(channel, [request])
            assert responses[0].error.code == (grpc.StatusCode.INVALID_ARGUMENT.value[0])
        finally:
            channel.close()
            _cleanup(bqemu_server)

    def test_finalize_default_proto_path_after_buffered_flow(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """BUFFERED Finalize ends the stream and makes further appends fail."""
        _setup_edge_table(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            stream = types.WriteStream.deserialize(
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(
                        types.CreateWriteStreamRequest(
                            parent="projects/write-edge/datasets/edge_ds/tables/tbl",
                            write_stream=types.WriteStream(
                                type_=types.WriteStream.Type.BUFFERED,
                            ),
                        ),
                    ),
                ),
            )
            channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                types.FinalizeWriteStreamRequest.serialize(
                    types.FinalizeWriteStreamRequest(name=stream.name),
                ),
            )
            # Follow-up Flush on a finalized buffered stream must fail.
            req = types.FlushRowsRequest(write_stream=stream.name, offset=1)
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/FlushRows")(
                    types.FlushRowsRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.OUT_OF_RANGE
        finally:
            channel.close()
            _cleanup(bqemu_server)
