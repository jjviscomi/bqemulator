"""``tabledata.insertAll`` throughput benchmark.

Measures rows/s for the REST streaming-insert path at four batch
sizes: 1, 10, 100, 1000 rows per call. The fixture creates an empty
target table once per session; each round inserts a fresh batch of
``N`` rows and the benchmark times the round-trip.

Per
[`ADR 0025 §1`](../../docs/adr/0025-perf-tier-design-contract.md)
this scenario covers the REST hot path used by every non-Storage-
Write writer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

pytestmark = pytest.mark.perf

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.server import EmulatorServer


_BATCH_SIZES = [1, 10, 100, 1000]


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
def insert_all_target(bqemu_server: EmulatorServer) -> str:
    """Create an empty ``perf.insert_all.target`` table for the scenario."""
    from google.cloud import bigquery

    client = _bq_client(bqemu_server)
    try:
        client.get_dataset("insert_all")
    except Exception:  # noqa: BLE001 — bigquery client surface
        client.create_dataset("insert_all")

    table_id = "perf.insert_all.target"
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


@pytest.mark.parametrize("batch_size", _BATCH_SIZES)
def test_insert_all_throughput(
    benchmark: Callable[..., None],
    bqemu_server: EmulatorServer,
    insert_all_target: str,
    batch_size: int,
) -> None:
    """Insert ``batch_size`` rows per round; report rows/s.

    The benchmark callable issues one ``insertAll`` per round and
    waits for the response. Per-round wall-clock dominated by HTTP
    + REST routing for ``batch_size=1``, by DuckDB write cost for
    ``batch_size=1000`` — both bounds are useful to track.
    """
    client = _bq_client(bqemu_server)
    # Pre-build the row payload so the timed block excludes Python
    # dict construction; the benchmark measures REST throughput,
    # not list-comprehension speed.
    rows = [{"id": i, "value": f"row_{i:08d}"} for i in range(batch_size)]

    # Use a monotonically-increasing counter so each round's rows are
    # unique (avoids any silent dedup by id if the engine adds one).
    counter = {"offset": 0}

    def _round() -> int:
        offset = counter["offset"]
        counter["offset"] += batch_size
        batch = [{"id": r["id"] + offset, "value": r["value"]} for r in rows]
        errors = client.insert_rows_json(insert_all_target, batch)
        if errors:  # pragma: no cover — fixture failure
            msg = f"insert_rows_json returned errors: {errors}"
            raise RuntimeError(msg)
        return len(batch)

    benchmark(_round)
    # Surface rows/s in pytest-benchmark's Extra Info column so a
    # baseline diff shows the throughput delta directly.
    median_s = benchmark.stats.stats.median  # type: ignore[attr-defined]
    if median_s > 0:
        benchmark.extra_info["rows_per_s"] = round(batch_size / median_s, 0)  # type: ignore[attr-defined]
        benchmark.extra_info["batch_size"] = batch_size  # type: ignore[attr-defined]
