"""Unit tests for table REST routes via FastAPI TestClient."""

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
def _with_dataset(app: FastAPI) -> None:
    """Create a dataset so table operations have a parent."""
    c = TestClient(app)
    c.post("/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "ds"}})


class TestListTables:
    def test_empty_dataset(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        r = c.get("/bigquery/v2/projects/p/datasets/ds/tables")
        assert r.status_code == 200
        assert r.json()["tables"] == []


class TestInsertTable:
    def test_create_with_schema(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"tableId": "t1"},
                "schema": {
                    "fields": [
                        {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                        {"name": "name", "type": "STRING"},
                    ],
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["tableReference"]["tableId"] == "t1"
        assert len(body["schema"]["fields"]) == 2

    def test_duplicate_returns_409(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        payload = {
            "tableReference": {"tableId": "dup"},
            "schema": {"fields": [{"name": "x", "type": "INT64"}]},
        }
        c.post("/bigquery/v2/projects/p/datasets/ds/tables", json=payload)
        r = c.post("/bigquery/v2/projects/p/datasets/ds/tables", json=payload)
        assert r.status_code == 409


class TestGetTable:
    def test_not_found_returns_404(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/bigquery/v2/projects/p/datasets/ds/tables/ghost")
        assert r.status_code == 404

    def test_get_existing(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"tableId": "t2"},
                "schema": {"fields": [{"name": "x", "type": "STRING"}]},
            },
        )
        r = c.get("/bigquery/v2/projects/p/datasets/ds/tables/t2")
        assert r.status_code == 200
        assert r.json()["tableReference"]["tableId"] == "t2"


class TestPatchTable:
    def test_patch_description(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"tableId": "tp"},
                "schema": {"fields": [{"name": "x", "type": "INT64"}]},
            },
        )
        r = c.patch(
            "/bigquery/v2/projects/p/datasets/ds/tables/tp",
            json={"description": "patched"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "patched"


class TestDeleteTable:
    def test_delete_existing(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"tableId": "td"},
                "schema": {"fields": [{"name": "x", "type": "INT64"}]},
            },
        )
        r = c.delete("/bigquery/v2/projects/p/datasets/ds/tables/td")
        assert r.status_code == 204

    def test_delete_not_found_returns_404(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.delete("/bigquery/v2/projects/p/datasets/ds/tables/ghost")
        assert r.status_code == 404


class TestPutTable:
    """The PUT (full-replace) variant of update_table — distinct from PATCH."""

    def test_put_existing_replaces(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"tableId": "tput"},
                "schema": {"fields": [{"name": "x", "type": "INT64"}]},
                "description": "before",
            },
        )
        r = c.put(
            "/bigquery/v2/projects/p/datasets/ds/tables/tput",
            json={
                "tableReference": {"tableId": "tput"},
                "schema": {"fields": [{"name": "x", "type": "INT64"}]},
                "description": "after",
            },
        )
        assert r.status_code == 200
        assert r.json()["description"] == "after"

    def test_put_nonexistent_returns_404(self, app: FastAPI, _with_dataset: None) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.put(
            "/bigquery/v2/projects/p/datasets/ds/tables/ghost",
            json={
                "tableReference": {"tableId": "ghost"},
                "schema": {"fields": [{"name": "x", "type": "INT64"}]},
            },
        )
        assert r.status_code == 404
