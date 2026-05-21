"""Tests for ``GzipRequestMiddleware`` — gzipped request-body decoding.

The Google Cloud Java BigQuery client gzips POST/PUT/PATCH bodies above
a small threshold; real BigQuery decodes them transparently. These tests
lock in that the emulator matches that behaviour on the request path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import gzip
import json

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


def _create_dataset_payload(project: str, dataset: str) -> bytes:
    payload = {
        "datasetReference": {"projectId": project, "datasetId": dataset},
        "location": "US",
    }
    return json.dumps(payload).encode("utf-8")


class TestGzipRequestMiddleware:
    def test_gzipped_post_body_is_decoded(self, live_context: AppContext) -> None:
        """A POST with ``Content-Encoding: gzip`` is decompressed transparently."""
        app = create_app(live_context)
        client = TestClient(app)
        body = _create_dataset_payload("gzip-proj", "gzip_ds")
        compressed = gzip.compress(body)
        response = client.post(
            "/bigquery/v2/projects/gzip-proj/datasets",
            content=compressed,
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["datasetReference"]["datasetId"] == "gzip_ds"

    def test_identity_encoding_passes_through(
        self,
        live_context: AppContext,
    ) -> None:
        """``Content-Encoding: identity`` is a documented no-op."""
        app = create_app(live_context)
        client = TestClient(app)
        body = _create_dataset_payload("identity-proj", "identity_ds")
        response = client.post(
            "/bigquery/v2/projects/identity-proj/datasets",
            content=body,
            headers={
                "Content-Encoding": "identity",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200, response.text

    def test_uncompressed_body_passes_through(
        self,
        live_context: AppContext,
    ) -> None:
        """No Content-Encoding header → middleware is a no-op."""
        app = create_app(live_context)
        client = TestClient(app)
        body = _create_dataset_payload("plain-proj", "plain_ds")
        response = client.post(
            "/bigquery/v2/projects/plain-proj/datasets",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text

    def test_unsupported_encoding_returns_415(
        self,
        live_context: AppContext,
    ) -> None:
        """A non-gzip, non-identity encoding is rejected with 415."""
        app = create_app(live_context)
        client = TestClient(app)
        response = client.post(
            "/bigquery/v2/projects/p/datasets",
            content=b"\x00\x01\x02",
            headers={
                "Content-Encoding": "deflate",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 415
        body = response.json()
        assert body["error"]["status"] == "UNSUPPORTED_MEDIA_TYPE"

    def test_malformed_gzip_returns_400(
        self,
        live_context: AppContext,
    ) -> None:
        """Bogus bytes under ``Content-Encoding: gzip`` surface as 400."""
        app = create_app(live_context)
        client = TestClient(app)
        response = client.post(
            "/bigquery/v2/projects/p/datasets",
            content=b"not actually gzip",
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["status"] == "INVALID_ARGUMENT"

    def test_get_requests_are_unaffected(
        self,
        live_context: AppContext,
    ) -> None:
        """GET requests have no body — middleware must not interfere."""
        app = create_app(live_context)
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
