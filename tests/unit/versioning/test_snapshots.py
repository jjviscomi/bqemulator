"""Unit tests for :mod:`bqemulator.versioning.snapshots`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import OutOfRangeError
from bqemulator.domain.events import EventBus, TableDataChanged
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_schema, quoted_table_ref
from bqemulator.versioning.snapshots import SnapshotManager
from tests.conftest import build_snapshot_manager

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def engine(ephemeral_settings) -> AsyncIterator[DuckDBEngine]:
    eng = DuckDBEngine(ephemeral_settings)
    await eng.start()
    try:
        yield eng
    finally:
        await eng.stop()


@pytest.fixture
def catalog() -> MemoryCatalogRepository:
    return MemoryCatalogRepository()


@pytest_asyncio.fixture
async def manager(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> SnapshotManager:
    return build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)


def _make_dataset(catalog: MemoryCatalogRepository, project: str, ds: str, now: datetime) -> None:
    catalog.create_dataset(
        DatasetMeta(
            project_id=project,
            dataset_id=ds,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project, ds, str(now)),
        ),
    )


def _make_table(
    catalog: MemoryCatalogRepository,
    project: str,
    ds: str,
    table: str,
    now: datetime,
) -> None:
    catalog.create_table(
        TableMeta(
            project_id=project,
            dataset_id=ds,
            table_id=table,
            table_type="TABLE",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project, ds, table, str(now)),
        ),
    )


def _create_physical_table(engine: DuckDBEngine, project: str, ds: str, table: str) -> None:
    schema_ref = quoted_schema(project, ds)
    engine.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ref}")
    engine.execute(f"CREATE TABLE {quoted_table_ref(project, ds, table)} (id INT64, name VARCHAR)")


async def test_capture_creates_snapshot_table_and_catalog_row(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")
    engine.execute(f"INSERT INTO {quoted_table_ref('p', 'ds', 't')} VALUES (1, 'a')")

    snap = manager.capture("p", "ds", "t", kind="AUTO")
    assert snap is not None
    assert snap.kind == "AUTO"
    assert snap.expires_at == now + timedelta(days=7)
    assert snap.duckdb_schema == "_bqemulator_snapshots"

    rows = engine.execute(
        f'SELECT * FROM "{snap.duckdb_schema}"."{snap.duckdb_table}"',
    ).fetchall()
    assert rows == [(1, "a")]


async def test_capture_user_kind_never_expires(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")

    snap = manager.capture("p", "ds", "t", kind="USER")
    assert snap is not None
    assert snap.expires_at is None


async def test_capture_returns_none_when_table_missing(manager: SnapshotManager) -> None:
    assert manager.capture("p", "ds", "missing") is None


async def test_record_change_publishes_event(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    received: list[TableDataChanged] = []
    bus = EventBus()
    bus.subscribe(TableDataChanged, received.append)  # type: ignore[arg-type]
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock, events=bus)

    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")

    manager.record_change("p", "ds", "t")
    assert len(received) == 1
    assert received[0].project_id == "p"
    assert received[0].dataset_id == "ds"
    assert received[0].table_id == "t"


async def test_resolve_time_travel_future_target_rejected(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")
    target = now + timedelta(minutes=5)
    with pytest.raises(OutOfRangeError):
        manager.resolve_time_travel("p", "ds", "t", target)


async def test_resolve_time_travel_beyond_retention_rejected(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")
    beyond = now - timedelta(days=14)
    with pytest.raises(OutOfRangeError):
        manager.resolve_time_travel("p", "ds", "t", beyond)


async def test_resolve_time_travel_no_snapshots_returns_none(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)
    _create_physical_table(engine, "p", "ds", "t")
    target = now - timedelta(minutes=1)
    assert manager.resolve_time_travel("p", "ds", "t", target) is None


async def test_resolve_time_travel_picks_latest_le_target(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    engine.execute(f"INSERT INTO {quoted_table_ref('p', 'ds', 't')} VALUES (1, 'a')")
    snap1 = manager.capture("p", "ds", "t")
    assert snap1 is not None

    frozen_clock.advance(seconds=60)
    engine.execute(f"INSERT INTO {quoted_table_ref('p', 'ds', 't')} VALUES (2, 'b')")
    snap2 = manager.capture("p", "ds", "t")
    assert snap2 is not None

    # A target between snap1 and snap2 must resolve to snap1 (largest
    # snapshot_time <= target).
    between = snap1.snapshot_time + timedelta(seconds=10)
    resolved = manager.resolve_time_travel("p", "ds", "t", between)
    assert resolved is not None
    assert resolved.snapshot_id == snap1.snapshot_id


async def test_resolve_time_travel_target_before_first_snapshot_raises(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    # Advance well past base so the first snap is in the middle of the
    # retention window.
    frozen_clock.advance(minutes=30)
    snap = manager.capture("p", "ds", "t")
    assert snap is not None

    # Target 1 minute before snap1 but still inside retention → out of
    # range because there is no snapshot <= target.
    target = snap.snapshot_time - timedelta(minutes=5)
    with pytest.raises(OutOfRangeError):
        manager.resolve_time_travel("p", "ds", "t", target)


async def test_resolve_time_travel_accepts_naive_datetime(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    snap = manager.capture("p", "ds", "t")
    assert snap is not None

    frozen_clock.advance(seconds=1)
    naive = snap.snapshot_time.replace(tzinfo=None) + timedelta(seconds=0, microseconds=500)
    resolved = manager.resolve_time_travel("p", "ds", "t", naive)
    assert resolved is not None
    assert resolved.snapshot_id == snap.snapshot_id


async def test_purge_expired_drops_only_past_auto(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    auto = manager.capture("p", "ds", "t", kind="AUTO")
    user = manager.capture("p", "ds", "t", kind="USER")
    assert auto is not None and user is not None

    # Advance past retention.
    frozen_clock.advance(days=8)
    removed = manager.purge_expired()
    assert removed == 1

    snaps = catalog.list_snapshots_for_table("p", "ds", "t")
    assert [s.kind for s in snaps] == ["USER"]


async def test_drop_snapshot_removes_table_and_row(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    snap = manager.capture("p", "ds", "t")
    assert snap is not None

    manager.drop_snapshot(snap)

    assert catalog.list_snapshots_for_table("p", "ds", "t") == ()
    # Physical table should also be gone.
    count = engine.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE schema_name = ?",
        ["_bqemulator_snapshots"],
    ).fetchone()
    assert count is not None
    assert count[0] == 0


async def test_drop_snapshots_for_table_respects_include_user_flag(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    base = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", base)
    _make_table(catalog, "p", "ds", "t", base)
    _create_physical_table(engine, "p", "ds", "t")

    manager.capture("p", "ds", "t", kind="AUTO")
    manager.capture("p", "ds", "t", kind="USER")

    removed_auto_only = manager.drop_snapshots_for_table(
        "p",
        "ds",
        "t",
        include_user=False,
    )
    assert removed_auto_only == 1
    remaining = catalog.list_snapshots_for_table("p", "ds", "t")
    assert [s.kind for s in remaining] == ["USER"]

    removed_all = manager.drop_snapshots_for_table(
        "p",
        "ds",
        "t",
        include_user=True,
    )
    assert removed_all == 1
    assert catalog.list_snapshots_for_table("p", "ds", "t") == ()


async def test_gc_loop_survives_inner_error(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    # Monkey-patch purge_expired to raise once, then signal cancel.
    calls = {"n": 0}

    def _patched() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    manager.purge_expired = _patched  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await manager.run_gc_loop(interval_seconds=0)
    assert calls["n"] >= 2


def test_snapshot_ids_are_sortable(frozen_clock: FrozenClock) -> None:
    # Generating ids at monotonically advancing clock values yields
    # strings that sort correctly.
    from bqemulator.versioning.snapshots import _new_snapshot_id

    a = _new_snapshot_id(frozen_clock.now())
    frozen_clock.advance(seconds=1)
    b = _new_snapshot_id(frozen_clock.now())
    assert a < b


async def test_resolve_time_travel_naive_future_rejected(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    now_naive = frozen_clock.now().replace(tzinfo=None) + timedelta(minutes=1)
    _make_dataset(catalog, "p", "ds", frozen_clock.now())
    _make_table(catalog, "p", "ds", "t", frozen_clock.now())
    _create_physical_table(engine, "p", "ds", "t")
    with pytest.raises(OutOfRangeError):
        manager.resolve_time_travel("p", "ds", "t", now_naive)


async def test_drop_snapshot_rejects_quoted_identifier(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    from bqemulator.catalog.models import SnapshotMeta

    malicious = SnapshotMeta(
        snapshot_id="x",
        project_id="p",
        dataset_id="ds",
        table_id="t",
        snapshot_time=frozen_clock.now(),
        kind="AUTO",
        duckdb_schema='"bad',
        duckdb_table="ok",
        expires_at=None,
    )
    with pytest.raises(Exception):  # ValidationError is a DomainError subclass
        manager.drop_snapshot(malicious)


async def test_capture_clears_stale_table_row(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
) -> None:
    manager = build_snapshot_manager(engine, catalog=catalog, clock=frozen_clock)
    # Only catalog row — no physical DuckDB table.
    now = frozen_clock.now()
    _make_dataset(catalog, "p", "ds", now)
    _make_table(catalog, "p", "ds", "t", now)

    # Capture should propagate the engine error since the source table
    # doesn't physically exist.
    with pytest.raises(Exception):
        manager.capture("p", "ds", "t")


async def test_purge_noop_without_expired(
    engine: DuckDBEngine,
    catalog: MemoryCatalogRepository,
    frozen_clock: FrozenClock,
    manager: SnapshotManager,
) -> None:
    removed = manager.purge_expired()
    assert removed == 0
