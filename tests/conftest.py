"""Top-level test configuration.

Shared fixtures live here; subdirectories inherit them automatically.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.repository import CatalogRepository
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import Clock, FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A FrozenClock pinned at 2026-04-15T00:00:00Z."""
    return FrozenClock(datetime(2026, 4, 15, tzinfo=UTC))


@pytest.fixture
def ephemeral_settings() -> Settings:
    """Ephemeral settings suitable for a unit test."""
    return Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )


@pytest.fixture
def persistent_settings(tmp_path: object) -> Iterator[Settings]:
    """Persistent settings with a throwaway data directory."""
    return Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=tmp_path,  # type: ignore[arg-type]
        rest_port=0,
        grpc_port=0,
    )


@pytest.fixture
def udf_registry(ephemeral_settings: Settings) -> UDFRegistry:
    """UDFRegistry wired to the ephemeral settings."""
    return UDFRegistry(ephemeral_settings)


def build_snapshot_manager(
    engine: object,
    catalog: CatalogRepository | None = None,
    clock: FrozenClock | None = None,
    events: EventBus | None = None,
    *,
    retention_days: int = 7,
) -> SnapshotManager:
    """Shared builder used by the Phase 7 test suites."""
    return SnapshotManager(
        engine=engine,  # type: ignore[arg-type]
        catalog=catalog or MemoryCatalogRepository(),
        clock=clock or FrozenClock(datetime(2026, 4, 15, tzinfo=UTC)),
        events=events or EventBus(),
        retention_days=retention_days,
    )


def build_row_access_manager(
    catalog: CatalogRepository | None = None,
    clock: Clock | None = None,
) -> RowAccessPolicyManager:
    """Shared builder used by the Phase 8 test suites."""
    return RowAccessPolicyManager(
        catalog=catalog or MemoryCatalogRepository(),
        clock=clock or FrozenClock(datetime(2026, 4, 15, tzinfo=UTC)),
    )
