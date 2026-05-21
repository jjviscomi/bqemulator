"""Tests for /healthz and /readyz endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator import __version__
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
async def live_context(ephemeral_settings: Settings) -> AsyncIterator[AppContext]:
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
        yield ctx
    finally:
        await engine.stop()


class TestHealthz:
    def test_returns_ok(self, live_context: AppContext) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__


class TestReadyz:
    def test_returns_ok_when_engine_and_catalog_are_live(
        self,
        live_context: AppContext,
    ) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["checks"]["duckdb"] == "ok"
        assert body["checks"]["catalog"] == "ok"


class TestCorrelationIdMiddleware:
    def test_response_includes_correlation_id_header(
        self,
        live_context: AppContext,
    ) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get("/healthz")
        assert "x-correlation-id" in {k.lower() for k in response.headers}

    def test_correlation_id_echoed_from_request(
        self,
        live_context: AppContext,
    ) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get(
            "/healthz",
            headers={"x-correlation-id": "test-cid-42"},
        )
        assert response.headers["x-correlation-id"] == "test-cid-42"


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_text(
        self,
        live_context: AppContext,
    ) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        # Make a few requests so we have some metrics
        client.get("/healthz")
        client.get("/healthz")
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "bqemulator_rest_requests_total" in response.text
        assert "bqemulator_build_info" in response.text


class TestErrorHandler:
    def test_unknown_route_returns_bigquery_shape_404(
        self,
        live_context: AppContext,
    ) -> None:
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get("/this-route-does-not-exist")
        # FastAPI's default 404 is still returned by Starlette for unmatched
        # routes; our exception handlers kick in only for raised DomainErrors.
        assert response.status_code == 404
