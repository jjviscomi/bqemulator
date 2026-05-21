"""Row-access policy manager tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
    ValidationError,
)
from bqemulator.row_access.policy import RowAccessPolicyManager

pytestmark = pytest.mark.unit


@pytest.fixture
def manager_and_catalog() -> tuple[RowAccessPolicyManager, MemoryCatalogRepository]:
    catalog = MemoryCatalogRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", str(now)),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "t", str(now)),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="snap",
            table_type="SNAPSHOT",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "snap", str(now)),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="mv",
            table_type="MATERIALIZED_VIEW",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "mv", str(now)),
        ),
    )
    return RowAccessPolicyManager(catalog=catalog, clock=FrozenClock(now)), catalog


class TestCreate:
    def test_creates_policy(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, catalog = manager_and_catalog
        policy = manager.create(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@example.com",),
        )
        assert policy.policy_id == "eu_only"
        assert policy.filter_predicate == "region = 'EU'"
        assert policy.grantees == ("user:eu@example.com",)
        assert catalog.get_row_access_policy("p", "ds", "t", "eu_only") == policy

    def test_rejects_missing_table(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(NotFoundError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="missing",
                policy_id="x",
                filter_predicate="1=1",
                grantees=(),
            )

    @pytest.mark.parametrize("bad_policy_id", ["", "has space", "weird-chars!", "x" * 257])
    def test_rejects_invalid_policy_id(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
        bad_policy_id: str,
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id=bad_policy_id,
                filter_predicate="1=1",
                grantees=(),
            )

    @pytest.mark.parametrize(
        "bad_filter",
        [
            "",
            "  ",
            "DROP TABLE users",
            "1=1; DROP TABLE users",
            "EXISTS (SELECT 1 FROM other)",
            "(SELECT count(*) FROM x) > 0",
        ],
    )
    def test_rejects_invalid_filter(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
        bad_filter: str,
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="bad",
                filter_predicate=bad_filter,
                grantees=(),
            )

    def test_rejects_unparseable_filter(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="garbled",
                filter_predicate="this is not :: valid sql",
                grantees=(),
            )

    @pytest.mark.parametrize(
        "bad_grantee",
        [
            "",  # empty
            "noprefix",  # no prefix and not literal
            "user:",  # empty value after :
            "domain:",
        ],
    )
    def test_rejects_invalid_grantee(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
        bad_grantee: str,
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="bad",
                filter_predicate="1=1",
                grantees=(bad_grantee,),
            )

    def test_rejects_duplicate_grantee(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="dup",
                filter_predicate="1=1",
                grantees=("user:a@x", "user:a@x"),
            )

    def test_rejects_creating_on_snapshot(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(InvalidQueryError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="snap",
                policy_id="x",
                filter_predicate="1=1",
                grantees=("allUsers",),
            )

    def test_rejects_creating_on_materialized_view(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(InvalidQueryError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="mv",
                policy_id="x",
                filter_predicate="1=1",
                grantees=("allUsers",),
            )

    def test_rejects_duplicate_create(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        manager.create(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        with pytest.raises(AlreadyExistsError):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="x",
                filter_predicate="1=1",
                grantees=("allUsers",),
            )


class TestUpdate:
    def test_updates_existing(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        manager.create(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="x",
            filter_predicate="1=1",
            grantees=("user:a@x",),
        )
        updated = manager.update(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="x",
            filter_predicate="region = 'US'",
            grantees=("user:b@x",),
        )
        assert updated.filter_predicate == "region = 'US'"
        assert updated.grantees == ("user:b@x",)

    def test_rejects_missing(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(NotFoundError):
            manager.update(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="missing",
                filter_predicate="1=1",
                grantees=(),
            )


class TestDelete:
    def test_deletes_existing(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, catalog = manager_and_catalog
        manager.create(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        manager.delete(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="x",
        )
        assert catalog.get_row_access_policy("p", "ds", "t", "x") is None

    def test_delete_missing_raises_unless_not_found_ok(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(NotFoundError):
            manager.delete(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id="missing",
            )
        # not_found_ok suppresses the error
        manager.delete(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="missing",
            not_found_ok=True,
        )


class TestBatchDelete:
    def test_batch_delete_atomic(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, catalog = manager_and_catalog
        for pid in ("a", "b", "c"):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id=pid,
                filter_predicate="1=1",
                grantees=("allUsers",),
            )
        manager.batch_delete(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_ids=("a", "b"),
        )
        assert catalog.get_row_access_policy("p", "ds", "t", "a") is None
        assert catalog.get_row_access_policy("p", "ds", "t", "b") is None
        assert catalog.get_row_access_policy("p", "ds", "t", "c") is not None

    def test_batch_delete_fails_atomically_when_any_missing(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, catalog = manager_and_catalog
        manager.create(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            policy_id="a",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        with pytest.raises(NotFoundError):
            manager.batch_delete(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_ids=("a", "missing"),
            )
        # Atomic: no policies should have been deleted.
        assert catalog.get_row_access_policy("p", "ds", "t", "a") is not None

    def test_batch_delete_rejects_empty_ids(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        with pytest.raises(ValidationError):
            manager.batch_delete(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_ids=(),
            )


class TestList:
    def test_list_returns_sorted_by_id(
        self,
        manager_and_catalog: tuple[RowAccessPolicyManager, MemoryCatalogRepository],
    ) -> None:
        manager, _ = manager_and_catalog
        for pid in ("zeta", "alpha", "beta"):
            manager.create(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                policy_id=pid,
                filter_predicate="1=1",
                grantees=("allUsers",),
            )
        ids = [p.policy_id for p in manager.list_for_table("p", "ds", "t")]
        assert ids == ["alpha", "beta", "zeta"]
