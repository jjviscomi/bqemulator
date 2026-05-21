"""Unit tests for /routines REST routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def app(ephemeral_settings: Settings) -> AsyncIterator[FastAPI]:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    ctx = AppContext(
        settings=ephemeral_settings,
        clock=FrozenClock(),
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(ephemeral_settings),
        snapshots=SnapshotManager(
            engine=engine,
            catalog=catalog,
            clock=FrozenClock(),
            events=events,
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock()),
    )
    try:
        yield create_app(ctx)
    finally:
        await engine.stop()


@pytest.fixture
def client_with_dataset(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.post(
        "/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
    )
    return client


class TestRoutinesCRUD:
    def test_insert_list_get_delete(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        body = {
            "routineReference": {
                "projectId": "p",
                "datasetId": "ds",
                "routineId": "add_one",
            },
            "routineType": "SCALAR_FUNCTION",
            "language": "SQL",
            "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
            "returnType": {"typeKind": "INT64"},
            "definitionBody": "x + 1",
        }
        r = c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        assert r.status_code == 200
        assert r.json()["routineReference"]["routineId"] == "add_one"

        r = c.get("/bigquery/v2/projects/p/datasets/ds/routines")
        assert r.status_code == 200
        assert r.json()["totalItems"] == 1

        r = c.get("/bigquery/v2/projects/p/datasets/ds/routines/add_one")
        assert r.status_code == 200
        assert r.json()["definitionBody"] == "x + 1"

        r = c.delete("/bigquery/v2/projects/p/datasets/ds/routines/add_one")
        assert r.status_code == 204

        r = c.get("/bigquery/v2/projects/p/datasets/ds/routines/add_one")
        assert r.status_code == 404

    def test_insert_duplicate_conflicts(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": "f"},
            "routineType": "SCALAR_FUNCTION",
            "language": "SQL",
            "definitionBody": "1",
        }
        c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        r = c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        assert r.status_code == 409

    def test_patch_updates_definition(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": "f"},
            "routineType": "SCALAR_FUNCTION",
            "language": "SQL",
            "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
            "returnType": {"typeKind": "INT64"},
            "definitionBody": "x + 1",
        }
        c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        r = c.patch(
            "/bigquery/v2/projects/p/datasets/ds/routines/f",
            json={"definitionBody": "x + 2"},
        )
        assert r.status_code == 200
        assert r.json()["definitionBody"] == "x + 2"

    def test_put_replaces(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": "f"},
            "routineType": "SCALAR_FUNCTION",
            "language": "SQL",
            "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
            "returnType": {"typeKind": "INT64"},
            "definitionBody": "x + 1",
        }
        c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        new_body = dict(body)
        new_body["definitionBody"] = "x * 2"
        r = c.put("/bigquery/v2/projects/p/datasets/ds/routines/f", json=new_body)
        assert r.status_code == 200

    def test_patch_missing_404(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        r = c.patch(
            "/bigquery/v2/projects/p/datasets/ds/routines/nope",
            json={"definitionBody": "x"},
        )
        assert r.status_code == 404

    def test_delete_missing_404(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        r = c.delete("/bigquery/v2/projects/p/datasets/ds/routines/nope")
        assert r.status_code == 404

    def test_get_missing_404(self, client_with_dataset: TestClient) -> None:
        c = client_with_dataset
        r = c.get("/bigquery/v2/projects/p/datasets/ds/routines/nope")
        assert r.status_code == 404

    def test_insert_without_routine_id_fails(
        self,
        client_with_dataset: TestClient,
    ) -> None:
        c = client_with_dataset
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds"},
            "routineType": "SCALAR_FUNCTION",
            "language": "SQL",
            "definitionBody": "x",
        }
        r = c.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
        assert r.status_code == 400
