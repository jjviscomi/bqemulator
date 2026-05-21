"""Test FastAPI's RequestValidationError rendering to BigQuery shape."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
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


class _RequestBody(BaseModel):
    project_id: str
    amount: int


@pytest_asyncio.fixture
async def app_with_validation(
    ephemeral_settings: Settings,
) -> AsyncIterator[FastAPI]:
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
    app = create_app(ctx)

    @app.post("/echo")
    def _echo(body: _RequestBody) -> dict[str, object]:
        return {"ok": True, "project_id": body.project_id, "amount": body.amount}

    try:
        yield app
    finally:
        await engine.stop()


def test_missing_field_returns_bigquery_shape_400(
    app_with_validation: FastAPI,
) -> None:
    client = TestClient(app_with_validation, raise_server_exceptions=False)
    response = client.post("/echo", json={"project_id": "p"})  # missing `amount`
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["status"] == "INVALID_ARGUMENT"
    assert body["error"]["errors"][0]["reason"] == "invalid"


def test_wrong_type_returns_bigquery_shape_400(
    app_with_validation: FastAPI,
) -> None:
    client = TestClient(app_with_validation, raise_server_exceptions=False)
    response = client.post(
        "/echo",
        json={"project_id": "p", "amount": "not-a-number"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"


def test_valid_request_succeeds(app_with_validation: FastAPI) -> None:
    client = TestClient(app_with_validation)
    response = client.post("/echo", json={"project_id": "p", "amount": 42})
    assert response.status_code == 200
    assert response.json()["ok"] is True
