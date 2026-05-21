"""Tests for FastAPI exception → BigQuery ErrorProto mapping."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
    UnsupportedFeatureError,
)
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def app_with_error_routes(
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

    @app.get("/raise/invalid")
    def _raise_invalid() -> None:
        raise InvalidQueryError("bad SQL on line 3")

    @app.get("/raise/not-found")
    def _raise_not_found() -> None:
        raise NotFoundError("Not found: table:proj.sales.orders")

    @app.get("/raise/already-exists")
    def _raise_already_exists() -> None:
        raise AlreadyExistsError("Already Exists: dataset:proj.sales")

    @app.get("/raise/unsupported")
    def _raise_unsupported() -> None:
        raise UnsupportedFeatureError("BigQuery ML is out of scope")

    @app.get("/raise/unhandled")
    def _raise_unhandled() -> None:
        raise RuntimeError("boom")

    @app.post("/raise/needs-json")
    async def _needs_json(request: Request) -> dict:
        body = await request.json()
        return {"received": body}

    try:
        yield app
    finally:
        await engine.stop()


class TestDomainErrorHandlers:
    def test_invalid_query_returns_400_with_bigquery_shape(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.get("/raise/invalid")
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == 400
        assert body["error"]["status"] == "INVALID_ARGUMENT"
        assert body["error"]["errors"][0]["reason"] == "invalidQuery"
        assert "bad SQL on line 3" in body["error"]["message"]

    def test_not_found_returns_404(self, app_with_error_routes: FastAPI) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.get("/raise/not-found")
        assert response.status_code == 404
        assert response.json()["error"]["status"] == "NOT_FOUND"

    def test_already_exists_returns_409(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.get("/raise/already-exists")
        assert response.status_code == 409
        assert response.json()["error"]["status"] == "ALREADY_EXISTS"

    def test_unsupported_returns_501(self, app_with_error_routes: FastAPI) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.get("/raise/unsupported")
        assert response.status_code == 501
        assert response.json()["error"]["status"] == "UNIMPLEMENTED"


class TestUnhandledExceptions:
    def test_unhandled_exception_returns_generic_500(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.get("/raise/unhandled")
        assert response.status_code == 500
        body = response.json()
        assert body["error"]["status"] == "INTERNAL"
        # Stack trace must NOT be leaked to the client.
        assert "RuntimeError" not in body["error"]["message"]
        assert "boom" not in body["error"]["message"]


class TestJSONDecodeErrorHandler:
    """Empty/malformed bodies on JSON endpoints must surface 400, not 500.

    Regression: discovered during the pre-launch audit (2026-05-21). A Java
    HttpClient race against uvicorn HTTP/1.1 was sending requests with an
    apparent-empty body. The downstream ``await request.json()`` raised
    ``JSONDecodeError`` which fell through to the generic 500 handler —
    making a malformed-client-request look like an emulator outage.
    """

    def test_empty_body_returns_400_with_bigquery_shape(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.post("/raise/needs-json", content=b"")
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["status"] == "INVALID_ARGUMENT"
        assert "valid JSON" in body["error"]["message"]
        assert body["error"]["errors"][0]["reason"] == "invalid"
        assert body["error"]["errors"][0]["location"] == "body"

    def test_malformed_json_returns_400_with_bigquery_shape(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.post(
            "/raise/needs-json",
            content=b'{"this": "is "broken json',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["status"] == "INVALID_ARGUMENT"
        assert "valid JSON" in body["error"]["message"]

    def test_well_formed_body_unchanged(
        self,
        app_with_error_routes: FastAPI,
    ) -> None:
        client = TestClient(app_with_error_routes, raise_server_exceptions=False)
        response = client.post(
            "/raise/needs-json",
            json={"hello": "world"},
        )
        assert response.status_code == 200
        assert response.json() == {"received": {"hello": "world"}}
