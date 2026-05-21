"""Storage Read API throughput benchmark.

Measures rows/s + MiB/s for the Arrow IPC path through
``CreateReadSession`` → ``ReadRows`` (server-streaming) on a
100 K-row table. The fixture builds the table once per session and
the benchmark callable times only the gRPC streaming consumption.

Per
[`ADR 0025 §1`](../../docs/adr/0025-perf-tier-design-contract.md) the
Avro branch is omitted — Storage Read Avro support is listed as
🚧 Phase 4 in the
[compatibility matrix](../../docs/reference/compatibility-matrix.md)
and the emulator surfaces an UNIMPLEMENTED error at the servicer.
When AVRO ships, a sibling benchmark module + baseline entries land
in the same PR.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import grpc
import pyarrow as pa
import pytest

from tests.perf._fixtures import DEFAULT_THROUGHPUT_ROWS, make_test_dataset

pytestmark = pytest.mark.perf

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.server import EmulatorServer


_READ = "/google.cloud.bigquery.storage.v1.BigQueryRead"


@pytest.fixture(scope="session")
def storage_read_table(bqemu_server: EmulatorServer) -> str:
    """Build a 100 K-row table for the Storage Read scenario.

    Returns the fully-qualified ``project.dataset.table`` string.
    """
    return make_test_dataset(
        bqemu_server,
        project="perf",
        dataset="storage_read",
        table="data",
        row_count=DEFAULT_THROUGHPUT_ROWS,
    )


def _create_read_session(
    channel: grpc.Channel,
    table_ref: str,
) -> Any:
    from google.cloud.bigquery_storage_v1 import types

    request = types.CreateReadSessionRequest(
        parent="projects/perf",
        read_session=types.ReadSession(
            table=_table_to_read_session_path(table_ref),
            data_format=types.DataFormat.ARROW,
        ),
        max_stream_count=1,
    )
    request_bytes = types.CreateReadSessionRequest.serialize(request)
    response_bytes = channel.unary_unary(f"{_READ}/CreateReadSession")(request_bytes)
    return types.ReadSession.deserialize(response_bytes)


def _table_to_read_session_path(table_ref: str) -> str:
    """Convert ``project.dataset.table`` to the gRPC table path shape."""
    project, dataset, table = table_ref.split(".")
    return f"projects/{project}/datasets/{dataset}/tables/{table}"


def test_storage_read_arrow_throughput(
    benchmark: Callable[..., None],
    bqemu_server: EmulatorServer,
    storage_read_table: str,
) -> None:
    """Time the ``CreateReadSession`` + ``ReadRows`` server-stream consumption.

    The benchmark callable opens a fresh gRPC channel each round so
    per-round timing reflects an honest "client connects, reads all
    rows" flow. The channel close + session disposal happen *outside*
    the timed block.
    """
    from google.cloud.bigquery_storage_v1 import types

    def _round() -> tuple[int, int]:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            session = _create_read_session(channel, storage_read_table)
            read_request = types.ReadRowsRequest(read_stream=session.streams[0].name)
            read_bytes = types.ReadRowsRequest.serialize(read_request)
            total_rows = 0
            total_bytes = 0
            for resp_bytes in channel.unary_stream(f"{_READ}/ReadRows")(read_bytes):
                resp = types.ReadRowsResponse.deserialize(resp_bytes)
                batch = resp.arrow_record_batch.serialized_record_batch
                if not batch:
                    continue
                total_bytes += len(batch)
                reader = pa.ipc.open_stream(batch)
                total_rows += reader.read_all().num_rows
        finally:
            channel.close()
        return total_rows, total_bytes

    rows, total_bytes = benchmark(_round)
    # Sanity check — fixture should produce >0 rows; per-round bytes
    # should be non-trivial (>1 KiB) for a 100 K-row table.
    assert rows == DEFAULT_THROUGHPUT_ROWS, f"expected {DEFAULT_THROUGHPUT_ROWS} rows, got {rows}"
    assert total_bytes > 1024, f"expected non-trivial Arrow stream, got {total_bytes} bytes"

    # Surface the throughput in the pytest-benchmark "Extra info"
    # column so a baseline diff shows MiB/s drift directly. The
    # comparison gate still uses median wall-clock as its primary
    # metric.
    median_s = benchmark.stats.stats.median  # type: ignore[attr-defined]
    if median_s > 0:
        mib_s = (total_bytes / median_s) / (1024 * 1024)
        rows_per_s = rows / median_s
        benchmark.extra_info["mib_per_s"] = round(mib_s, 2)  # type: ignore[attr-defined]
        benchmark.extra_info["rows_per_s"] = round(rows_per_s, 0)  # type: ignore[attr-defined]
