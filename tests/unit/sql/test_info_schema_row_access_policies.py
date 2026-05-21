"""INFORMATION_SCHEMA.ROW_ACCESS_POLICIES expansion tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import RowAccessPolicyMeta
from bqemulator.sql.rewriter.information_schema import (
    expand_information_schema,
    expand_information_schema_row_access_policies,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def catalog_with_two_policies() -> MemoryCatalogRepository:
    cat = MemoryCatalogRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cat.create_row_access_policy(
        RowAccessPolicyMeta(
            project_id="p",
            dataset_id="ds",
            table_id="orders",
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x", "group:admins@x"),
            creation_time=now,
            last_modified_time=now,
            etag='"e"',
        ),
    )
    cat.create_row_access_policy(
        RowAccessPolicyMeta(
            project_id="p",
            dataset_id="ds",
            table_id="orders",
            policy_id="vip",
            filter_predicate="vip = TRUE",
            grantees=("allAuthenticatedUsers",),
            creation_time=now,
            last_modified_time=now,
            etag='"v"',
        ),
    )
    return cat


class TestExpansion:
    def test_dataset_qualified(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        assert "INFORMATION_SCHEMA" not in out
        assert "VALUES" in out
        # Both policies are emitted; ordering is by table+policy_id.
        assert "'eu_only'" in out
        assert "'vip'" in out

    def test_project_qualified(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM p.ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        assert "VALUES" in out
        assert "'eu_only'" in out

    def test_bare_returns_empty_set(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        # WHERE FALSE indicates the empty placeholder shape.
        assert "WHERE FALSE" in out

    def test_no_policies_emits_empty(self) -> None:
        cat = MemoryCatalogRepository()
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            cat,
        )
        assert "WHERE FALSE" in out

    def test_grantees_emitted_as_csv(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        assert "user:eu@x, group:admins@x" in out

    def test_short_circuit_when_no_information_schema(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        sql = "SELECT 1"
        assert (
            expand_information_schema_row_access_policies(
                sql,
                "p",
                catalog_with_two_policies,
            )
            == sql
        )

    def test_dataset_filter_excludes_other_datasets(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        # Add a policy in a different dataset; it should not appear.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog_with_two_policies.create_row_access_policy(
            RowAccessPolicyMeta(
                project_id="p",
                dataset_id="OTHER",
                table_id="t",
                policy_id="x",
                filter_predicate="1=1",
                grantees=(),
                creation_time=now,
                last_modified_time=now,
                etag='"x"',
            ),
        )
        out = expand_information_schema_row_access_policies(
            "SELECT * FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        assert "'x'" not in out  # other-dataset policy hidden

    def test_aggregate_expand_information_schema_includes_rap(
        self,
        catalog_with_two_policies: MemoryCatalogRepository,
    ) -> None:
        out = expand_information_schema(
            "SELECT * FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
            "p",
            catalog_with_two_policies,
        )
        assert "VALUES" in out
        assert "'eu_only'" in out
