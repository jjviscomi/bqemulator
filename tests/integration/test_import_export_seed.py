"""Integration tests: import → export → seed against the in-process emulator.

The flow:

1. Start an emulator with a persistent ``data_dir``.
2. Drive it via the BigQuery Python client to create datasets, tables,
   and rows.
3. Stop the emulator (releases the DuckDB lock).
4. Run ``run_export`` against the same ``data_dir``.
5. Run ``run_seed`` into a second ``data_dir``.
6. Start a fresh emulator on the seeded ``data_dir`` and verify the
   catalog + rows survived.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bqemulator.commands.export import run_export
from bqemulator.commands.seed import run_seed
from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def _start_persistent_emulator(data_dir: Path) -> tuple[EmulatorServer, object]:
    """Start an emulator persistent at ``data_dir`` on a background loop."""
    from bqemulator.testing._thread_runner import ThreadedEmulator

    threaded = ThreadedEmulator(
        Settings(
            persistence_mode=PersistenceMode.PERSISTENT,
            data_dir=data_dir,
            rest_port=0,
            grpc_port=0,
        ),
    )
    threaded.start()
    return threaded.server, threaded


@pytest.fixture
def bq_client() -> Iterator[type]:
    try:
        from google.cloud import bigquery
    except ImportError:  # pragma: no cover
        pytest.skip("google-cloud-bigquery not installed")
    return bigquery


def test_export_then_seed_round_trips_data(
    tmp_path: Path,
    bq_client: type,
) -> None:
    """Round-trip: emulator → export → seed → fresh emulator preserves rows."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"

    server, threaded = _start_persistent_emulator(src_dir)
    try:
        client = bq_client.Client(
            project="proj",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=server.rest_url),
        )
        try:
            client.create_dataset("ds")
            table_id = "proj.ds.rows"
            client.create_table(
                bq_client.Table(
                    table_id,
                    schema=[bq_client.SchemaField("n", "INT64")],
                ),
            )
            assert (
                client.insert_rows_json(
                    table_id,
                    [{"n": 1}, {"n": 2}, {"n": 3}],
                )
                == []
            )
        finally:
            client.close()
    finally:
        threaded.stop()

    out_dir = tmp_path / "export"
    summary = run_export(data_dir=src_dir, output_dir=out_dir)
    assert summary.tables >= 1
    assert summary.rows_written == 3

    seed_summary = run_seed(data_dir=dest_dir, input_dir=out_dir)
    assert seed_summary.rows_loaded == 3

    server2, threaded2 = _start_persistent_emulator(dest_dir)
    try:
        client = bq_client.Client(
            project="proj",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=server2.rest_url),
        )
        try:
            job = client.query("SELECT n FROM `proj.ds.rows` ORDER BY n")
            assert sorted(r.n for r in job.result()) == [1, 2, 3]
        finally:
            client.close()
    finally:
        threaded2.stop()
