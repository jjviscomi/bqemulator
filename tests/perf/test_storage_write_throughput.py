"""Storage Write API throughput benchmark.

Measures rows/s for the four stream types (DEFAULT, COMMITTED,
PENDING, BUFFERED) x two input formats (Arrow IPC, dynamic protobuf).
Per round, the benchmark opens a fresh ``AppendRows`` bidirectional
stream, writes a 1 000-row batch, and (for non-DEFAULT stream types)
finalises + commits.

Per
[`ADR 0025 §1`](../../docs/adr/0025-perf-tier-design-contract.md)
this scenario covers the gRPC streaming-insert path Phase 5
introduced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import grpc
import pytest

from tests.perf._fixtures import (
    make_arrow_payload,
    make_proto_descriptor,
    make_proto_rows,
)

pytestmark = pytest.mark.perf

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.server import EmulatorServer


_WRITE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"
_ROWS_PER_BATCH = 1_000


def _bq_client(bqemu_server: EmulatorServer) -> Any:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="perf",
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


@pytest.fixture(scope="session")
def storage_write_target(bqemu_server: EmulatorServer) -> str:
    """Create ``perf.storage_write.target`` with ``(id INT64, value STRING)``."""
    from google.cloud import bigquery

    client = _bq_client(bqemu_server)
    try:
        client.get_dataset("storage_write")
    except Exception:  # noqa: BLE001 — bigquery client surface
        client.create_dataset("storage_write")

    table_id = "perf.storage_write.target"
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("value", "STRING"),
    ]
    try:
        client.delete_table(table_id)
    except Exception:  # noqa: BLE001
        pass
    client.create_table(bigquery.Table(table_id, schema=schema))
    return table_id


def _create_write_stream(
    channel: grpc.Channel,
    table_id: str,
    stream_type: int,
) -> Any:
    """Issue ``CreateWriteStream`` for the requested type, return the WriteStream."""
    from google.cloud.bigquery_storage_v1 import types

    project, dataset, table = table_id.split(".")
    parent = f"projects/{project}/datasets/{dataset}/tables/{table}"
    request = types.CreateWriteStreamRequest(
        parent=parent,
        write_stream=types.WriteStream(type_=stream_type),
    )
    request_bytes = types.CreateWriteStreamRequest.serialize(request)
    response_bytes = channel.unary_unary(f"{_WRITE}/CreateWriteStream")(request_bytes)
    return types.WriteStream.deserialize(response_bytes)


def _default_stream_name(table_id: str) -> str:
    """Return the well-known DEFAULT stream name for a table."""
    project, dataset, table = table_id.split(".")
    return f"projects/{project}/datasets/{dataset}/tables/{table}/streams/_default"


def _build_arrow_request(
    stream_name: str,
    schema_bytes: bytes,
    batch_bytes: bytes,
    *,
    include_schema: bool,
    offset: int | None,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    arrow_rows = types.AppendRowsRequest.ArrowData(
        rows=types.ArrowRecordBatch(serialized_record_batch=batch_bytes),
    )
    if include_schema:
        arrow_rows.writer_schema = types.ArrowSchema(serialized_schema=schema_bytes)
    kwargs: dict[str, Any] = {"write_stream": stream_name, "arrow_rows": arrow_rows}
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


def _build_proto_request(
    stream_name: str,
    descriptor: Any,
    rows: list[bytes],
    *,
    include_schema: bool,
    offset: int | None,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    proto_data = types.AppendRowsRequest.ProtoData(
        rows=types.ProtoRows(serialized_rows=rows),
    )
    if include_schema:
        proto_data.writer_schema = types.ProtoSchema(proto_descriptor=descriptor)
    kwargs: dict[str, Any] = {"write_stream": stream_name, "proto_rows": proto_data}
    if offset is not None:
        kwargs["offset"] = offset
    return types.AppendRowsRequest(**kwargs)


def _drain_append_rows(channel: grpc.Channel, requests: list[Any]) -> list[Any]:
    from google.cloud.bigquery_storage_v1 import types

    def request_iter() -> Iterator[bytes]:
        for r in requests:
            yield types.AppendRowsRequest.serialize(r)

    return [
        types.AppendRowsResponse.deserialize(resp_bytes)
        for resp_bytes in channel.stream_stream(f"{_WRITE}/AppendRows")(request_iter())
    ]


# Mapping of stream-type label → ``WriteStream.Type`` integer. The
# enum import is deferred so this module imports without
# ``google-cloud-bigquery-storage`` (the integers themselves are
# stable wire-format constants).
_STREAM_TYPE_INTS = {
    # DEFAULT is type 0 but addressed by the ``_default`` magic stream
    # name rather than CreateWriteStream; the entry here is just a tag
    # for the parametrize id.
    "DEFAULT": 0,
    "COMMITTED": 1,
    "PENDING": 2,
    "BUFFERED": 3,
}


@pytest.mark.parametrize("stream_type_label", list(_STREAM_TYPE_INTS.keys()))
@pytest.mark.parametrize("payload_format", ["arrow", "proto"])
def test_storage_write_throughput(
    benchmark: Callable[..., None],
    bqemu_server: EmulatorServer,
    storage_write_target: str,
    stream_type_label: str,
    payload_format: str,
) -> None:
    """Time a 1 000-row AppendRows batch on each (stream_type x payload) cell.

    Per-round flow:

    1. Open a fresh gRPC channel (outside timed block).
    2. (Non-DEFAULT only) CreateWriteStream.
    3. Build the AppendRows request with the schema-on-first-message
       contract.
    4. Stream the request through ``AppendRows``; consume the responses.
    5. (PENDING only) FinalizeWriteStream + BatchCommitWriteStreams.

    Steps 2-5 are the timed block; channel setup + teardown are
    outside.
    """
    pytest.importorskip(
        "google.cloud.bigquery_storage_v1",
        reason="google-cloud-bigquery-storage required for Storage Write benchmarks",
    )
    from google.cloud.bigquery_storage_v1 import types

    ids = list(range(_ROWS_PER_BATCH))
    values = [f"row_{i:08d}" for i in ids]
    schema_bytes, batch_bytes = make_arrow_payload(ids, values)
    descriptor = make_proto_descriptor()
    proto_rows = make_proto_rows(ids, values)

    stream_type_int = _STREAM_TYPE_INTS[stream_type_label]
    counter = {"offset": 0}

    def _round() -> int:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            if stream_type_label == "DEFAULT":
                stream_name = _default_stream_name(storage_write_target)
                offset = None  # DEFAULT streams ignore offsets
            else:
                stream = _create_write_stream(
                    channel,
                    storage_write_target,
                    stream_type_int,
                )
                stream_name = stream.name
                offset = counter["offset"]
                counter["offset"] += _ROWS_PER_BATCH

            if payload_format == "arrow":
                request = _build_arrow_request(
                    stream_name,
                    schema_bytes,
                    batch_bytes,
                    include_schema=True,
                    offset=offset,
                )
            else:
                request = _build_proto_request(
                    stream_name,
                    descriptor,
                    proto_rows,
                    include_schema=True,
                    offset=offset,
                )

            responses = _drain_append_rows(channel, [request])
            assert len(responses) >= 1

            if stream_type_label == "PENDING":
                # Finalize + commit so PENDING rows actually land — this
                # cost is part of the stream type's wall-clock contract.
                fin_req = types.FinalizeWriteStreamRequest(name=stream_name)
                channel.unary_unary(f"{_WRITE}/FinalizeWriteStream")(
                    types.FinalizeWriteStreamRequest.serialize(fin_req),
                )
                project, dataset, _table = storage_write_target.split(".")
                commit_req = types.BatchCommitWriteStreamsRequest(
                    parent=f"projects/{project}/datasets/{dataset}",
                    write_streams=[stream_name],
                )
                channel.unary_unary(f"{_WRITE}/BatchCommitWriteStreams")(
                    types.BatchCommitWriteStreamsRequest.serialize(commit_req),
                )
        finally:
            channel.close()
        return _ROWS_PER_BATCH

    benchmark(_round)
    median_s = benchmark.stats.stats.median  # type: ignore[attr-defined]
    if median_s > 0:
        benchmark.extra_info["rows_per_s"] = round(  # type: ignore[attr-defined]
            _ROWS_PER_BATCH / median_s,
            0,
        )
        benchmark.extra_info["stream_type"] = stream_type_label  # type: ignore[attr-defined]
        benchmark.extra_info["payload_format"] = payload_format  # type: ignore[attr-defined]
