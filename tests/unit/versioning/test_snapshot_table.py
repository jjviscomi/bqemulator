"""Unit tests for :mod:`bqemulator.versioning.snapshot_table`."""

from __future__ import annotations

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
)
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.versioning.snapshot_table import SnapshotTableManager

pytestmark = pytest.mark.unit


async def test_create_snapshot_table_materialises_user_kind_snapshot(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a"), (2, "b")])

    manager = SnapshotTableManager(full_ctx)
    meta = await manager.create("p", "ds", "snap_t", "p", "ds", "t")
    assert meta.table_type == "SNAPSHOT"
    assert meta.base_table == "p.ds.t"
    assert meta.snapshot_time is not None

    snaps = full_ctx.catalog.list_snapshots_for_table("p", "ds", "t")
    assert len(snaps) == 1
    assert snaps[0].kind == "USER"
    assert snaps[0].expires_at is None


async def test_drop_snapshot_table_clears_physical_and_catalog(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])

    manager = SnapshotTableManager(full_ctx)
    await manager.create("p", "ds", "snap_t", "p", "ds", "t")

    await manager.drop("p", "ds", "snap_t")

    assert full_ctx.catalog.get_table("p", "ds", "snap_t") is None
    assert full_ctx.catalog.list_snapshots_for_table("p", "ds", "t") == ()
    # Physical table should be gone.
    rows = full_ctx.engine.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'snap_t'",
    ).fetchone()
    assert rows is not None and rows[0] == 0


async def test_create_rejects_existing_destination(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    make_table("p", "ds", "snap_t")
    manager = SnapshotTableManager(full_ctx)
    with pytest.raises(AlreadyExistsError):
        await manager.create("p", "ds", "snap_t", "p", "ds", "t")


async def test_create_rejects_missing_source(
    full_ctx: AppContext,
    make_dataset,
) -> None:
    make_dataset("p", "ds")
    manager = SnapshotTableManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.create("p", "ds", "snap_t", "p", "ds", "missing")


async def test_drop_rejects_non_snapshot_table(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    manager = SnapshotTableManager(full_ctx)
    with pytest.raises(InvalidQueryError):
        await manager.drop("p", "ds", "t")


async def test_drop_rejects_missing_table(
    full_ctx: AppContext,
    make_dataset,
) -> None:
    make_dataset("p", "ds")
    manager = SnapshotTableManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.drop("p", "ds", "missing")


async def test_create_in_other_dataset(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "src")
    make_dataset("p", "snaps")
    make_table("p", "src", "t", rows=[(1, "a")])

    manager = SnapshotTableManager(full_ctx)
    await manager.create("p", "snaps", "t_2026_04_15", "p", "src", "t")

    rows = full_ctx.engine.execute(
        f"SELECT id FROM {quoted_table_ref('p', 'snaps', 't_2026_04_15')}",
    ).fetchall()
    assert rows == [(1,)]
