"""Cover the readyz failure paths."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

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


class _BrokenCatalog(MemoryCatalogRepository):
    def list_datasets(self, project_id: str) -> tuple[object, ...]:  # type: ignore[override]  # noqa: ARG002
        raise RuntimeError("catalog broken")


@pytest.mark.asyncio
async def test_readyz_returns_503_when_catalog_broken(
    ephemeral_settings: Settings,
) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    try:
        ctx = AppContext(
            settings=ephemeral_settings,
            clock=FrozenClock(),
            engine=engine,
            catalog=_BrokenCatalog(),  # type: ignore[arg-type]
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
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert "catalog broken" in body["checks"]["catalog"]
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_readyz_returns_503_when_duckdb_broken(
    ephemeral_settings: Settings,
) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog2 = MemoryCatalogRepository()
    events2 = EventBus()
    try:
        await engine.stop()  # close connection so health query fails

        ctx = AppContext(
            settings=ephemeral_settings,
            clock=FrozenClock(),
            engine=engine,
            catalog=catalog2,
            metrics=MetricsRegistry(),
            events=events2,
            udf_registry=UDFRegistry(ephemeral_settings),
            snapshots=SnapshotManager(
                engine=engine,
                catalog=catalog2,
                clock=FrozenClock(),
                events=events2,
                retention_days=7,
            ),
            row_access=RowAccessPolicyManager(catalog=catalog2, clock=FrozenClock()),
        )
        app = create_app(ctx)
        client = TestClient(app)
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert "error" in response.json()["checks"]["duckdb"]
    finally:
        # Already stopped
        pass
