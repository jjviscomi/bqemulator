"""Property tests for snapshot retention + MV staleness propagation.

The combinatorial surface here is:

* Multiple snapshots captured at varying timestamps under POST-change
  semantics — the largest ``snapshot_time ≤ target`` lookup must always
  return the right one and never skip an eligible row.
* Multiple base-table changes against an MV — *any* event whose target
  matches a registered base table must flip the MV to ``is_stale=True``,
  and the flip must be idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    MaterializedViewMeta,
    SnapshotMeta,
    TableMeta,
)
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import OutOfRangeError
from bqemulator.domain.events import EventBus, TableDataChanged
from tests.conftest import build_snapshot_manager

pytestmark = pytest.mark.property


@st.composite
def _snapshot_seconds(draw: st.DrawFn, count: int) -> list[int]:
    """Generate ``count`` strictly increasing second offsets."""
    raw = draw(
        st.lists(
            st.integers(min_value=0, max_value=86400),
            min_size=count,
            max_size=count,
            unique=True,
        ),
    )
    return sorted(raw)


def _seed_table(catalog: MemoryCatalogRepository, now: datetime) -> None:
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds"),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "t"),
        ),
    )


def _record_snapshot(
    catalog: MemoryCatalogRepository,
    snap_time: datetime,
    suffix: str,
    *,
    kind: str = "AUTO",
    expires_at: datetime | None = None,
) -> SnapshotMeta:
    meta = SnapshotMeta(
        snapshot_id=f"s_{suffix}",
        project_id="p",
        dataset_id="ds",
        table_id="t",
        snapshot_time=snap_time,
        kind=kind,  # type: ignore[arg-type]
        duckdb_schema="_bqemulator_snapshots",
        duckdb_table=f"s_{suffix}",
        expires_at=expires_at,
    )
    catalog.create_snapshot(meta)
    return meta


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(seconds=_snapshot_seconds(count=5), pick_offset=st.integers(min_value=-3, max_value=8))
def test_resolve_returns_largest_snapshot_le_target(
    seconds: list[int],
    pick_offset: int,
) -> None:
    """For any timeline, the resolver picks the largest snap ≤ target."""
    base = datetime(2026, 4, 24, tzinfo=UTC)
    catalog = MemoryCatalogRepository()
    _seed_table(catalog, base)

    snap_times = [base + timedelta(seconds=s) for s in seconds]
    for i, st_ in enumerate(snap_times):
        _record_snapshot(catalog, st_, suffix=f"{i:02d}")

    # Pick a target second between the snap times. ``pick_offset``
    # selects an index; the target is the snap at that index plus a
    # tiny offset so we straddle the boundary.
    if pick_offset < 0:
        target = snap_times[0] - timedelta(seconds=1)
    elif pick_offset >= len(snap_times):
        target = snap_times[-1] + timedelta(seconds=1)
    else:
        target = snap_times[pick_offset] + timedelta(microseconds=1)

    # Anchor "now" past the most recent snap so the retention check passes.
    clock = FrozenClock(snap_times[-1] + timedelta(seconds=10))
    manager = build_snapshot_manager(
        engine=None,
        catalog=catalog,
        clock=clock,
        retention_days=90,
    )
    if target < snap_times[0]:
        with pytest.raises(OutOfRangeError):
            manager.resolve_time_travel("p", "ds", "t", target)
        return
    resolved = manager.resolve_time_travel("p", "ds", "t", target)
    assert resolved is not None
    # Largest snap_time <= target.
    expected = max(s for s in snap_times if s <= target)
    assert resolved.snapshot_time == expected


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(seconds=_snapshot_seconds(count=4), retention_days=st.integers(min_value=1, max_value=14))
def test_purge_drops_only_expired_auto_snapshots(
    seconds: list[int],
    retention_days: int,
) -> None:
    """Past-retention AUTO snapshots are dropped; USER snapshots survive."""
    base = datetime(2026, 4, 24, tzinfo=UTC)
    catalog = MemoryCatalogRepository()
    _seed_table(catalog, base)

    auto_metas: list[SnapshotMeta] = []
    user_metas: list[SnapshotMeta] = []
    for i, s in enumerate(seconds):
        snap_time = base + timedelta(seconds=s)
        expires = snap_time + timedelta(days=retention_days)
        auto_metas.append(
            _record_snapshot(catalog, snap_time, suffix=f"a{i:02d}", expires_at=expires),
        )
        user_metas.append(
            _record_snapshot(catalog, snap_time, suffix=f"u{i:02d}", kind="USER"),
        )

    # Advance the clock past the last expiration.
    clock = FrozenClock(auto_metas[-1].expires_at + timedelta(seconds=1))  # type: ignore[arg-type]
    manager = build_snapshot_manager(
        engine=_StubEngine(),
        catalog=catalog,
        clock=clock,
        retention_days=retention_days,
    )
    removed = manager.purge_expired()
    assert removed == len(auto_metas)
    remaining = catalog.list_snapshots_for_table("p", "ds", "t")
    assert {s.kind for s in remaining} == {"USER"}


class _StubEngine:
    """Minimal stand-in for the DuckDB engine used by purge_expired."""

    def execute(self, _sql: str, _parameters: list | None = None) -> _StubEngine:
        return self


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    base_count=st.integers(min_value=1, max_value=5),
    event_indices=st.lists(
        st.integers(min_value=0, max_value=10),
        min_size=0,
        max_size=20,
    ),
)
def test_table_data_changed_marks_mv_stale_idempotently(
    base_count: int,
    event_indices: list[int],
) -> None:
    """Any event matching a base table must flip is_stale=True; idempotent."""
    from bqemulator.versioning.materialized_views import _make_stale_handler

    base = datetime(2026, 4, 24, tzinfo=UTC)
    catalog = MemoryCatalogRepository()
    _seed_table(catalog, base)
    bases = [("p", "ds", f"base_{i}") for i in range(base_count)]
    for _, _, t in bases:
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id=t,
                creation_time=base,
                last_modified_time=base,
                etag=generate_etag("p", "ds", t),
            ),
        )
    mv = MaterializedViewMeta(
        project_id="p",
        dataset_id="ds",
        table_id="mv",
        view_query="SELECT 1",
        base_tables=tuple(bases),
        last_refresh_time=base,
        is_stale=False,
    )
    catalog.upsert_materialized_view(mv)

    bus = EventBus()

    class _StubCtx:
        def __init__(self) -> None:
            self.catalog = catalog
            self.events = bus

    ctx = _StubCtx()
    handler = _make_stale_handler(ctx, mv)  # type: ignore[arg-type]
    bus.subscribe(TableDataChanged, handler)

    matched_any = False
    for idx in event_indices:
        target = f"base_{idx}" if idx < base_count else f"unrelated_{idx}"
        bus.publish(TableDataChanged("p", "ds", target))
        if idx < base_count:
            matched_any = True

    final = catalog.get_materialized_view("p", "ds", "mv")
    assert final is not None
    assert final.is_stale is matched_any
