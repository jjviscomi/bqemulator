"""Integration test: ``/admin/*`` endpoints against the in-process emulator.

Runs the full server (REST + gRPC) with ``admin_enabled=True`` and the
default ephemeral persistence; uses the BigQuery Python client to seed
realistic state (dataset, table, query job) and confirms the admin
endpoints reflect it.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_server() -> Iterator[EmulatorServer]:
    from bqemulator.streaming import read_session
    from bqemulator.testing._thread_runner import ThreadedEmulator

    # The read-session registry is a module-level singleton (a pragmatic
    # carry-over from Phase 4). Tests that ran before us may have left
    # entries in it; clear at fixture entry so the admin endpoint sees
    # only the state this test creates.
    read_session._SESSIONS.clear()
    threaded = ThreadedEmulator(
        Settings(
            admin_enabled=True,
            persistence_mode=PersistenceMode.EPHEMERAL,
            rest_port=0,
            grpc_port=0,
        ),
    )
    threaded.start()
    try:
        yield threaded.server
    finally:
        threaded.stop()


def test_admin_catalog_reports_created_dataset_and_table(
    admin_server: EmulatorServer,
) -> None:
    base = admin_server.rest_url
    # Create dataset + table via REST.
    httpx.post(
        f"{base}/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "d"}},
        timeout=10.0,
    ).raise_for_status()
    httpx.post(
        f"{base}/bigquery/v2/projects/p/datasets/d/tables",
        json={
            "schema": {
                "fields": [{"name": "id", "type": "INT64", "mode": "REQUIRED"}],
            },
            "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
        },
        timeout=10.0,
    ).raise_for_status()

    resp = httpx.get(f"{base}/admin/catalog", timeout=10.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalProjects"] == 1
    dataset = body["projects"][0]["datasets"][0]
    assert dataset["datasetId"] == "d"
    assert dataset["tables"][0]["tableId"] == "t"


def test_admin_jobs_after_query(admin_server: EmulatorServer) -> None:
    base = admin_server.rest_url
    httpx.post(
        f"{base}/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "d"}},
        timeout=10.0,
    ).raise_for_status()
    httpx.post(
        f"{base}/bigquery/v2/projects/p/jobs",
        json={
            "configuration": {
                "query": {"query": "SELECT 1", "useLegacySql": False},
            },
        },
        timeout=10.0,
    ).raise_for_status()

    resp = httpx.get(f"{base}/admin/jobs", timeout=10.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalItems"] >= 1


def test_admin_config_dumps_settings(admin_server: EmulatorServer) -> None:
    resp = httpx.get(f"{admin_server.rest_url}/admin/config", timeout=10.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["admin_enabled"] is True


def test_admin_streams_empty_by_default(admin_server: EmulatorServer) -> None:
    resp = httpx.get(f"{admin_server.rest_url}/admin/streams", timeout=10.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["writeStreamCount"] == 0
    assert body["readSessionCount"] == 0
