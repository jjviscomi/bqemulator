"""Tests for MemoryCatalogRepository.

These also exercise the CatalogRepository contract, which the DuckDB
implementation is expected to match.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    MaterializedViewMeta,
    RoutineMeta,
    RowAccessPolicyMeta,
    SnapshotMeta,
    TableMeta,
)
from bqemulator.domain.errors import AlreadyExistsError, NotFoundError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def make_dataset(project: str = "p", dataset: str = "sales") -> DatasetMeta:
    return DatasetMeta(
        project_id=project,
        dataset_id=dataset,
        creation_time=NOW,
        last_modified_time=NOW,
        etag=f"etag-{project}-{dataset}",
    )


def make_table(
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
        etag=f"etag-{table}",
    )


class TestDatasetCrud:
    def test_list_empty(self) -> None:
        repo = MemoryCatalogRepository()
        assert repo.list_datasets("p") == ()

    def test_create_then_get(self) -> None:
        repo = MemoryCatalogRepository()
        d = repo.create_dataset(make_dataset())
        assert repo.get_dataset("p", "sales") == d

    def test_create_duplicate_raises(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        with pytest.raises(AlreadyExistsError):
            repo.create_dataset(make_dataset())

    def test_list_scoped_by_project(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset("a", "s1"))
        repo.create_dataset(make_dataset("a", "s2"))
        repo.create_dataset(make_dataset("b", "s3"))

        a_ds = repo.list_datasets("a")
        assert {d.dataset_id for d in a_ds} == {"s1", "s2"}
        b_ds = repo.list_datasets("b")
        assert {d.dataset_id for d in b_ds} == {"s3"}

    def test_update_replaces_existing(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        updated = make_dataset().model_copy(update={"description": "new desc"})
        repo.update_dataset(updated)
        assert repo.get_dataset("p", "sales").description == "new desc"  # type: ignore[union-attr]

    def test_update_missing_raises(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.update_dataset(make_dataset())

    def test_delete_empty_dataset(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.delete_dataset("p", "sales")
        assert repo.get_dataset("p", "sales") is None

    def test_delete_missing_raises_by_default(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.delete_dataset("p", "sales")

    def test_delete_missing_not_found_ok(self) -> None:
        repo = MemoryCatalogRepository()
        repo.delete_dataset("p", "sales", not_found_ok=True)  # no raise

    def test_delete_non_empty_requires_cascade(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
        with pytest.raises(NotFoundError):
            repo.delete_dataset("p", "sales")

    def test_delete_contents_cascades(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
        repo.delete_dataset("p", "sales", delete_contents=True)
        assert repo.get_dataset("p", "sales") is None
        assert repo.list_tables("p", "sales") == ()

    def test_delete_contents_cascades_row_access_policies(self) -> None:
        """``delete_dataset(delete_contents=True)`` must cascade to RAPs.

        Without this cascade, deleting+recreating the same
        ``(project, dataset, table)`` triple leaves the previous RAP in
        the catalog. The next REST POST against the same table returns
        409 Conflict because the policy key already exists. Caught by
        the cross-language E2E row_access seeded-fixture re-run in P2.d
        follow-up #2.
        """
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
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
        assert repo.list_row_access_policies("p", "sales", "orders")

        repo.delete_dataset("p", "sales", delete_contents=True)
        assert repo.list_row_access_policies("p", "sales", "orders") == ()

    def test_delete_contents_cascades_materialized_views(self) -> None:
        """``delete_dataset(delete_contents=True)`` must cascade to MVs."""
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
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
        assert repo.get_materialized_view("p", "sales", "orders_mv") is not None

        repo.delete_dataset("p", "sales", delete_contents=True)
        assert repo.get_materialized_view("p", "sales", "orders_mv") is None

    def test_delete_contents_cascades_snapshots(self) -> None:
        """``delete_dataset(delete_contents=True)`` must cascade to snapshots."""
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
        repo.create_snapshot(
            SnapshotMeta(
                snapshot_id="snap-1",
                project_id="p",
                dataset_id="sales",
                table_id="orders",
                snapshot_time=NOW,
                kind="USER",
                duckdb_schema="p__sales",
                duckdb_table="orders_snapshot_1",
            ),
        )
        assert repo.list_snapshots_for_table("p", "sales", "orders")

        repo.delete_dataset("p", "sales", delete_contents=True)
        assert repo.list_snapshots_for_table("p", "sales", "orders") == ()
        assert repo.list_all_snapshots() == ()


class TestTableCrud:
    def test_create_requires_parent_dataset(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.create_table(make_table())

    def test_create_and_get(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        t = repo.create_table(make_table())
        assert repo.get_table("p", "sales", "orders") == t

    def test_list_scoped(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table(table="orders"))
        repo.create_table(make_table(table="customers"))
        assert {t.table_id for t in repo.list_tables("p", "sales")} == {
            "orders",
            "customers",
        }

    def test_duplicate_raises(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
        with pytest.raises(AlreadyExistsError):
            repo.create_table(make_table())

    def test_update_missing_raises(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.update_table(make_table())

    def test_delete_table(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        repo.create_table(make_table())
        repo.delete_table("p", "sales", "orders")
        assert repo.get_table("p", "sales", "orders") is None

    def test_delete_missing_not_found_ok(self) -> None:
        repo = MemoryCatalogRepository()
        repo.delete_table("p", "sales", "orders", not_found_ok=True)


class TestRoutineCrud:
    def _make_routine(self) -> RoutineMeta:
        return RoutineMeta(
            project_id="p",
            dataset_id="sales",
            routine_id="SafeDivide",
            routine_type="SCALAR_FUNCTION",
            language="SQL",
            definition_body="IF(b = 0, NULL, a / b)",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )

    def test_create_requires_parent_dataset(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.create_routine(self._make_routine())

    def test_crud_roundtrip(self) -> None:
        repo = MemoryCatalogRepository()
        repo.create_dataset(make_dataset())
        r = repo.create_routine(self._make_routine())
        assert repo.get_routine("p", "sales", "SafeDivide") == r
        assert repo.list_routines("p", "sales") == (r,)
        repo.delete_routine("p", "sales", "SafeDivide")
        assert repo.get_routine("p", "sales", "SafeDivide") is None


class TestJobRegistry:
    def _make_job(self, job_id: str = "j-1") -> object:
        from bqemulator.catalog.models import JobMeta

        return JobMeta(
            project_id="p",
            job_id=job_id,
            job_type="QUERY",
            state="DONE",
            configuration={},
            creation_time=NOW,
            etag=f"etag-{job_id}",
        )

    def test_upsert_then_get(self) -> None:
        repo = MemoryCatalogRepository()
        j = self._make_job()
        repo.upsert_job(j)  # type: ignore[arg-type]
        assert repo.get_job("p", "j-1") is j

    def test_upsert_overrides_existing(self) -> None:
        repo = MemoryCatalogRepository()
        repo.upsert_job(self._make_job())  # type: ignore[arg-type]
        repo.upsert_job(self._make_job())  # type: ignore[arg-type]
        assert repo.get_job("p", "j-1") is not None

    def test_list_jobs_respects_max_results(self) -> None:
        repo = MemoryCatalogRepository()
        for i in range(5):
            repo.upsert_job(self._make_job(f"j-{i}"))  # type: ignore[arg-type]
        assert len(repo.list_jobs("p", max_results=3)) == 3

    def test_delete_job(self) -> None:
        repo = MemoryCatalogRepository()
        repo.upsert_job(self._make_job())  # type: ignore[arg-type]
        repo.delete_job("p", "j-1")
        assert repo.get_job("p", "j-1") is None

    def test_delete_missing_raises_by_default(self) -> None:
        repo = MemoryCatalogRepository()
        with pytest.raises(NotFoundError):
            repo.delete_job("p", "j-1")
