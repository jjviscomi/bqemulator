"""Tests for the DuckDB-backed CatalogRepository.

Phase 0 implementation delegates to an in-memory cache; the DuckDB
tables are created via migrations but not yet written through. These
tests exercise the delegation path and the migration hook.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    MaterializedViewMeta,
    RoutineMeta,
    RowAccessPolicyMeta,
    SnapshotMeta,
    TableMeta,
)
from bqemulator.config import Settings
from bqemulator.domain.errors import NotFoundError
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _ds(project: str = "p", dataset: str = "sales") -> DatasetMeta:
    return DatasetMeta(
        project_id=project,
        dataset_id=dataset,
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


def _table(
    project: str = "p",
    dataset: str = "sales",
    table: str = "orders",
) -> TableMeta:
    return TableMeta(
        project_id=project,
        dataset_id=dataset,
        table_id=table,
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


def _routine(
    project: str = "p",
    dataset: str = "sales",
    routine: str = "r1",
) -> RoutineMeta:
    return RoutineMeta(
        project_id=project,
        dataset_id=dataset,
        routine_id=routine,
        routine_type="SCALAR_FUNCTION",
        language="SQL",
        definition_body="x",
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


@pytest.mark.asyncio
async def test_ensure_ready_runs_migrations(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        repo.ensure_ready()
        # Idempotent
        repo.ensure_ready()
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_dataset_crud_roundtrips(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        assert repo.list_datasets("p") == ()

        repo.create_dataset(_ds())
        assert repo.get_dataset("p", "sales") is not None
        assert len(repo.list_datasets("p")) == 1

        updated = _ds().model_copy(update={"description": "d"})
        repo.update_dataset(updated)
        assert repo.get_dataset("p", "sales").description == "d"  # type: ignore[union-attr]

        repo.delete_dataset("p", "sales")
        assert repo.get_dataset("p", "sales") is None
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_table_crud_roundtrips(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        repo.create_dataset(_ds())
        repo.create_table(_table())
        assert repo.list_tables("p", "sales") == (_table(),)
        assert repo.get_table("p", "sales", "orders") == _table()

        updated = _table().model_copy(update={"description": "d"})
        repo.update_table(updated)
        assert repo.get_table("p", "sales", "orders").description == "d"  # type: ignore[union-attr]

        repo.delete_table("p", "sales", "orders")
        assert repo.get_table("p", "sales", "orders") is None
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_routine_crud_roundtrips(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        repo.create_dataset(_ds())
        r = repo.create_routine(_routine())
        assert repo.get_routine("p", "sales", "r1") == r
        assert repo.list_routines("p", "sales") == (r,)

        updated = _routine().model_copy(update={"description": "d"})
        repo.update_routine(updated)
        assert repo.get_routine("p", "sales", "r1").description == "d"  # type: ignore[union-attr]

        repo.delete_routine("p", "sales", "r1")
        assert repo.get_routine("p", "sales", "r1") is None
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_jobs_upsert_and_list(ephemeral_settings: Settings) -> None:
    from bqemulator.catalog.models import JobMeta

    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        for i in range(3):
            repo.upsert_job(
                JobMeta(
                    project_id="p",
                    job_id=f"j-{i}",
                    job_type="QUERY",
                    state="DONE",
                    configuration={},
                    creation_time=NOW,
                    etag=f"e-{i}",
                ),
            )
        assert len(repo.list_jobs("p")) == 3
        repo.delete_job("p", "j-0")
        assert repo.get_job("p", "j-0") is None

        with pytest.raises(NotFoundError):
            repo.delete_job("p", "missing")
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_delete_dataset_not_found_ok(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        repo.delete_dataset("p", "missing", not_found_ok=True)
        repo.delete_table("p", "missing", "tbl", not_found_ok=True)
        repo.delete_routine("p", "missing", "r", not_found_ok=True)
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_delete_dataset_cascades_through_persistent_tables(
    ephemeral_settings: Settings,
) -> None:
    """``delete_dataset(delete_contents=True)`` removes every dataset-scoped row.

    Phase 7 (snapshots, materialized_views, mv_dependencies) and
    Phase 8 (row_access_policies) catalog tables must be cleaned up
    when a dataset is dropped, otherwise re-creating the same
    ``(project, dataset, table)`` triple finds a leftover row and a
    REST ``POST`` collides with 409 Conflict. Caught by the
    cross-language E2E row_access seeded-fixture re-run in P2.d
    follow-up #2.
    """
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        repo = DuckDBCatalogRepository(engine)
        repo.create_dataset(_ds())
        repo.create_table(
            TableMeta(
                project_id="p",
                dataset_id="sales",
                table_id="orders",
                creation_time=NOW,
                last_modified_time=NOW,
                etag="etag-orders",
            ),
        )
        repo.create_row_access_policy(
            RowAccessPolicyMeta(
                project_id="p",
                dataset_id="sales",
                table_id="orders",
                policy_id="eu_only",
                filter_predicate="region = 'EU'",
                grantees=("user:eu@example.com",),
                creation_time=NOW,
                last_modified_time=NOW,
                etag="rap-1",
            ),
        )
        repo.create_snapshot(
            SnapshotMeta(
                snapshot_id="snap-1",
                project_id="p",
                dataset_id="sales",
                table_id="orders",
                snapshot_time=NOW,
                kind="USER",
                duckdb_schema="p__sales",
                duckdb_table="orders_snap",
            ),
        )
        repo.upsert_materialized_view(
            MaterializedViewMeta(
                project_id="p",
                dataset_id="sales",
                table_id="orders_mv",
                view_query="SELECT id FROM `p.sales.orders`",
                base_tables=(("p", "sales", "orders"),),
                last_refresh_time=NOW,
            ),
        )

        # Drop the dataset — every dependent row should go with it.
        repo.delete_dataset("p", "sales", delete_contents=True)

        # Catalog cache should be cleared.
        assert repo.list_row_access_policies("p", "sales", "orders") == ()
        assert repo.list_snapshots_for_table("p", "sales", "orders") == ()
        assert repo.get_materialized_view("p", "sales", "orders_mv") is None

        # The persistent DuckDB-side rows must also be cleared so a
        # fresh repo on the same engine doesn't rehydrate stale state.
        rebuilt = DuckDBCatalogRepository(engine)
        assert rebuilt.list_row_access_policies("p", "sales", "orders") == ()
        assert rebuilt.list_snapshots_for_table("p", "sales", "orders") == ()
        assert rebuilt.get_materialized_view("p", "sales", "orders_mv") is None
    finally:
        await engine.stop()
