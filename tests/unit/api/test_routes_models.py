"""Unit tests for /models REST routes.

The Models resource has no ``insert`` route, so tests seed models
directly through the catalog (the path ``CREATE MODEL`` will use) and
then exercise list / get / patch / delete over HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, ModelMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)
_BASE = "/bigquery/v2/projects/p/datasets/ds/models"


@pytest_asyncio.fixture
async def app(ephemeral_settings: Settings) -> AsyncIterator[FastAPI]:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    ctx = AppContext(
        settings=ephemeral_settings,
        clock=FrozenClock(NOW),
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(ephemeral_settings),
        snapshots=SnapshotManager(
            engine=engine,
            catalog=catalog,
            clock=FrozenClock(NOW),
            events=events,
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock(NOW)),
    )
    try:
        yield create_app(ctx)
    finally:
        await engine.stop()


def _seed_dataset(app: FastAPI, dataset_id: str = "ds") -> None:
    app.state.context.catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id=dataset_id,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )


def _seed_model(app: FastAPI, model_id: str = "m1", **overrides: object) -> ModelMeta:
    fields: dict[str, object] = {
        "project_id": "p",
        "dataset_id": "ds",
        "model_id": model_id,
        "model_type": "LINEAR_REGRESSION",
        "feature_columns": ({"name": "x", "type": {"typeKind": "FLOAT64"}},),
        "label_columns": ({"name": "y", "type": {"typeKind": "FLOAT64"}},),
        "labels": {"team": "ds"},
        "description": "seed",
        "training_query": "SELECT x, y FROM ds.t",
        "creation_time": NOW,
        "last_modified_time": NOW,
        "etag": f"etag-{model_id}",
    }
    fields.update(overrides)
    model = ModelMeta(**fields)  # type: ignore[arg-type]
    return app.state.context.catalog.create_model(model)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    _seed_dataset(app)
    return TestClient(app)


class TestModelsRead:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get(_BASE)
        assert r.status_code == 200
        assert r.json() == {"models": []}

    def test_list_and_get(self, client: TestClient) -> None:
        _seed_model(client.app)  # type: ignore[arg-type]
        r = client.get(_BASE)
        assert r.status_code == 200
        body = r.json()
        assert len(body["models"]) == 1
        assert "nextPageToken" not in body
        assert body["models"][0]["modelReference"] == {
            "projectId": "p",
            "datasetId": "ds",
            "modelId": "m1",
        }

        r = client.get(f"{_BASE}/m1")
        assert r.status_code == 200
        got = r.json()
        assert got["modelType"] == "LINEAR_REGRESSION"
        assert got["featureColumns"][0]["name"] == "x"
        assert got["labelColumns"][0]["type"] == {"typeKind": "FLOAT64"}

    def test_get_missing_404(self, client: TestClient) -> None:
        r = client.get(f"{_BASE}/nope")
        assert r.status_code == 404
        assert r.json()["error"]["status"] == "NOT_FOUND"

    def test_list_orders_by_model_id(self, client: TestClient) -> None:
        _seed_model(client.app, "m2")  # type: ignore[arg-type]
        _seed_model(client.app, "m1")  # type: ignore[arg-type]
        ids = [m["modelReference"]["modelId"] for m in client.get(_BASE).json()["models"]]
        assert ids == ["m1", "m2"]


class TestModelsPatch:
    def test_patch_updates_mutable_field(self, client: TestClient) -> None:
        _seed_model(client.app)  # type: ignore[arg-type]
        r = client.patch(f"{_BASE}/m1", json={"description": "updated", "friendlyName": "Churn"})
        assert r.status_code == 200
        body = r.json()
        assert body["description"] == "updated"
        assert body["friendlyName"] == "Churn"

    def test_patch_ignores_read_only_model_type(self, client: TestClient) -> None:
        _seed_model(client.app)  # type: ignore[arg-type]
        r = client.patch(f"{_BASE}/m1", json={"modelType": "KMEANS"})
        assert r.status_code == 200
        assert r.json()["modelType"] == "LINEAR_REGRESSION"

    def test_patch_sets_and_clears_expiration(self, client: TestClient) -> None:
        _seed_model(client.app)  # type: ignore[arg-type]
        r = client.patch(f"{_BASE}/m1", json={"expirationTime": "1799999999000"})
        assert r.json()["expirationTime"] == "1799999999000"
        r = client.patch(f"{_BASE}/m1", json={"expirationTime": None})
        assert "expirationTime" not in r.json()

    def test_patch_missing_404(self, client: TestClient) -> None:
        r = client.patch(f"{_BASE}/nope", json={"description": "x"})
        assert r.status_code == 404


class TestModelsDelete:
    def test_delete_then_404(self, client: TestClient) -> None:
        _seed_model(client.app)  # type: ignore[arg-type]
        assert client.delete(f"{_BASE}/m1").status_code == 204
        assert client.get(f"{_BASE}/m1").status_code == 404

    def test_delete_missing_404(self, client: TestClient) -> None:
        assert client.delete(f"{_BASE}/nope").status_code == 404
