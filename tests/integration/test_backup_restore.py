"""Integration test: backup → restore against the in-process emulator.

Verifies that ``run_backup`` + ``run_restore`` round-trip a working
persistent emulator (catalog + table rows).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bqemulator.commands.backup import run_backup
from bqemulator.commands.restore import run_restore
from bqemulator.config import PersistenceMode, Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def bq_client() -> Iterator[type]:
    try:
        from google.cloud import bigquery
    except ImportError:  # pragma: no cover
        pytest.skip("google-cloud-bigquery not installed")
    return bigquery


def test_backup_restore_round_trips_via_real_emulator(
    tmp_path: Path,
    bq_client: type,
) -> None:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    from bqemulator.testing._thread_runner import ThreadedEmulator

    src = tmp_path / "src"
    backup_dir = tmp_path / "backup"
    dest = tmp_path / "dest"

    # Seed source emulator.
    threaded = ThreadedEmulator(
        Settings(
            persistence_mode=PersistenceMode.PERSISTENT,
            data_dir=src,
            rest_port=0,
            grpc_port=0,
        ),
    )
    threaded.start()
    try:
        client = bq_client.Client(
            project="p",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=threaded.server.rest_url),
        )
        try:
            client.create_dataset("d")
            client.create_table(
                bq_client.Table(
                    "p.d.t",
                    schema=[bq_client.SchemaField("n", "INT64")],
                ),
            )
            assert client.insert_rows_json("p.d.t", [{"n": 7}, {"n": 11}]) == []
        finally:
            client.close()
    finally:
        threaded.stop()

    # Backup → restore.
    run_backup(data_dir=src, output_dir=backup_dir)
    run_restore(data_dir=dest, input_dir=backup_dir)

    # Verify against a fresh emulator on dest.
    threaded2 = ThreadedEmulator(
        Settings(
            persistence_mode=PersistenceMode.PERSISTENT,
            data_dir=dest,
            rest_port=0,
            grpc_port=0,
        ),
    )
    threaded2.start()
    try:
        client = bq_client.Client(
            project="p",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=threaded2.server.rest_url),
        )
        try:
            job = client.query("SELECT n FROM `p.d.t` ORDER BY n")
            assert sorted(r.n for r in job.result()) == [7, 11]
        finally:
            client.close()
    finally:
        threaded2.stop()
