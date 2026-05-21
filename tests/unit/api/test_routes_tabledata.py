"""Unit tests for tabledata REST routes (insertAll + list)."""

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
def _with_table(app: FastAPI) -> None:
    """Create dataset + table for tabledata tests."""
    c = TestClient(app)
    c.post("/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "td"}})
    c.post(
        "/bigquery/v2/projects/p/datasets/td/tables",
        json={
            "tableReference": {"tableId": "items"},
            "schema": {
                "fields": [
                    {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                    {"name": "label", "type": "STRING"},
                ],
            },
        },
    )


class TestInsertAll:
    def test_insert_rows(self, app: FastAPI, _with_table: None) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/datasets/td/tables/items/insertAll",
            json={
                "rows": [
                    {"json": {"id": 1, "label": "first"}},
                    {"json": {"id": 2, "label": "second"}},
                ],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["insertErrors"] == []

    def test_insert_empty_rows(self, app: FastAPI, _with_table: None) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/datasets/td/tables/items/insertAll",
            json={"rows": []},
        )
        assert r.status_code == 200
        assert r.json()["insertErrors"] == []

    def test_insert_to_missing_table_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/datasets/td/tables/ghost/insertAll",
            json={"rows": [{"json": {"x": 1}}]},
        )
        assert r.status_code == 404


class TestListTabledata:
    def test_list_after_insert(self, app: FastAPI, _with_table: None) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets/td/tables/items/insertAll",
            json={
                "rows": [
                    {"json": {"id": 1, "label": "a"}},
                    {"json": {"id": 2, "label": "b"}},
                ],
            },
        )
        r = c.get("/bigquery/v2/projects/p/datasets/td/tables/items/data")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#tableDataList"
        assert int(body["totalRows"]) == 2
        assert len(body["rows"]) == 2

    def test_list_missing_table_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/bigquery/v2/projects/p/datasets/td/tables/ghost/data")
        assert r.status_code == 404
