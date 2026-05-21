"""Integration tests: Storage Write API hardening (Phase 5 audit).

These tests are paired with the bugs fixed in the production-readiness
audit so a future regression would trip CI.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
from typing import Any

import grpc
import pyarrow as pa
import pyarrow.ipc
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration

_WRITE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"


def _client(bqemu_server: EmulatorServer) -> Any:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="harden-proj",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup(bqemu_server: EmulatorServer) -> None:
    from google.cloud import bigquery

    c = _client(bqemu_server)
    try:
        c.get_dataset("harden_ds")
    except Exception:  # noqa: BLE001
        c.create_dataset("harden_ds")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING"),
    ]
    try:
        c.get_table("harden-proj.harden_ds.tbl")
    except Exception:  # noqa: BLE001
        c.create_table(
            bigquery.Table("harden-proj.harden_ds.tbl", schema=schema),
        )


def _cleanup(bqemu_server: EmulatorServer) -> None:
    try:
        _client(bqemu_server).delete_dataset(
            "harden_ds",
            delete_contents=True,
            not_found_ok=True,
        )
    except Exception:  # noqa: BLE001
        pass


def _arrow_payload(n_rows: int, value_size: int = 4) -> tuple[bytes, bytes]:
    ids = list(range(n_rows))
    names = ["x" * value_size] * n_rows
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "name": pa.array(names, type=pa.string()),
        }
    )
    schema_sink = io.BytesIO()
    w = pa.ipc.new_stream(schema_sink, table.schema)
    w.close()
    schema_bytes = schema_sink.getvalue()[:-8]

    full_sink = io.BytesIO()
    w = pa.ipc.new_stream(full_sink, table.schema)
    for batch in table.to_batches():
        w.write_batch(batch)
    w.close()
    full_bytes = full_sink.getvalue()
    return schema_bytes, full_bytes[len(schema_bytes) :]


def _arrow_request(
    stream: str, schema_bytes: bytes, batch_bytes: bytes, offset: int | None = None
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
# SQL injection
# ---------------------------------------------------------------------------


class TestInjectionDefense:
    def test_injection_in_project_id_on_create_stream(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """A malicious project id cannot reach DuckDB via CreateWriteStream."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            req = types.CreateWriteStreamRequest(
                parent='projects/p";DROP TABLE x;--/datasets/d/tables/t',
                write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                    types.CreateWriteStreamRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()


# ---------------------------------------------------------------------------
# Message size cap
# ---------------------------------------------------------------------------


class TestMessageSizeCap:
    def test_oversized_append_returns_resource_exhausted(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """AppendRows over the configured byte cap is rejected.

        We temporarily shrink the cap so the test doesn't need to
        build a literal 10 MiB payload.
        """
        _setup(bqemu_server)
        # Shrink cap to the configured 1024-byte floor so a modest payload
        # trivially exceeds it. Pydantic enforces the floor, so we don't
        # accept arbitrarily small values here.
        original_cap = bqemu_server._settings.write_api_max_request_bytes  # type: ignore[attr-defined]
        bqemu_server._settings.write_api_max_request_bytes = 1024  # type: ignore[attr-defined]
        try:
            from google.cloud.bigquery_storage_v1 import types

            channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
            try:
                # 200 rows x ~32-byte values -> comfortably > 1 KiB serialized.
                schema_bytes, batch_bytes = _arrow_payload(200, value_size=32)
                # Create a COMMITTED stream to append to.
                stream = types.WriteStream.deserialize(
                    channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                        types.CreateWriteStreamRequest.serialize(
                            types.CreateWriteStreamRequest(
                                parent="projects/harden-proj/datasets/harden_ds/tables/tbl",
                                write_stream=types.WriteStream(
                                    type_=types.WriteStream.Type.COMMITTED,
                                ),
                            ),
                        ),
                    ),
                )
                responses = _append_rows(
                    channel,
                    [_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
                )
                assert len(responses) == 1
                assert responses[0].error.code == grpc.StatusCode.RESOURCE_EXHAUSTED.value[0]
            finally:
                channel.close()
        finally:
            bqemu_server._settings.write_api_max_request_bytes = original_cap  # type: ignore[attr-defined]
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# Buffer cap for PENDING streams
# ---------------------------------------------------------------------------


class TestBufferCap:
    def test_pending_rejects_beyond_buffer_cap(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """PENDING stream rejects an append that would exceed the row cap."""
        _setup(bqemu_server)
        original_cap = bqemu_server._settings.write_api_max_stream_rows  # type: ignore[attr-defined]
        bqemu_server._settings.write_api_max_stream_rows = 10  # type: ignore[attr-defined]
        try:
            from google.cloud.bigquery_storage_v1 import types

            channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
            try:
                stream = types.WriteStream.deserialize(
                    channel.unary_unary(f"{_WRITE}/CreateWriteStream")(
                        types.CreateWriteStreamRequest.serialize(
                            types.CreateWriteStreamRequest(
                                parent="projects/harden-proj/datasets/harden_ds/tables/tbl",
                                write_stream=types.WriteStream(
                                    type_=types.WriteStream.Type.PENDING,
                                ),
                            ),
                        ),
                    ),
                )
                # 15 rows — 5 past the cap of 10.
                schema_bytes, batch_bytes = _arrow_payload(15)
                responses = _append_rows(
                    channel,
                    [_arrow_request(stream.name, schema_bytes, batch_bytes, offset=0)],
                )
                assert len(responses) == 1
                assert responses[0].error.code == grpc.StatusCode.RESOURCE_EXHAUSTED.value[0]
            finally:
                channel.close()
        finally:
            bqemu_server._settings.write_api_max_stream_rows = original_cap  # type: ignore[attr-defined]
            _cleanup(bqemu_server)


# ---------------------------------------------------------------------------
# Metrics cleanup on stream deletion (unit-style against the servicer).
# ---------------------------------------------------------------------------


class TestMetricsCleanup:
    def test_deleting_stream_decrements_gauge(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Manager.delete releases the active-stream gauge slot.

        Exercise the abandon path directly against a servicer instance
        wired up to the live context, so we test the full gauge-cleanup
        loop without needing a public hook on the live server.
        """
        _setup(bqemu_server)
        from bqemulator.grpc_api.write_servicer import BigQueryWriteHandler
        from bqemulator.streaming.write_stream import WriteStreamType

        context = bqemu_server._context  # type: ignore[attr-defined]
        assert context is not None
        handler = BigQueryWriteHandler(context)

        gauge = context.metrics.write_streams_active.labels(stream_type="COMMITTED")
        before = gauge._value.get()

        stream = handler.manager.create(
            project_id="harden-proj",
            dataset_id="harden_ds",
            table_id="tbl",
            stream_id="abcd1234",
            stream_type=WriteStreamType.COMMITTED,
        )
        # Manually bump the gauge + mark the stream as "metric-registered"
        # to simulate what _handle_create_write_stream does.
        gauge.inc()
        stream.metric_registered = True
        assert gauge._value.get() == before + 1

        # Abandon the stream (client disconnected, cleanup task runs, etc.).
        handler.manager.delete(stream.name)
        assert gauge._value.get() == before

        _cleanup(bqemu_server)
