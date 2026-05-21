"""Row-access SQL rewriter tests — see ADR 0018."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    AccessEntry,
    DatasetMeta,
    RowAccessPolicyMeta,
    TableMeta,
)
from bqemulator.row_access.identity import DEFAULT_CALLER, CallerIdentity
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access

pytestmark = pytest.mark.unit


@pytest.fixture
def catalog() -> MemoryCatalogRepository:
    cat = MemoryCatalogRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cat.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag='"a"',
        ),
    )
    cat.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="orders",
            creation_time=now,
            last_modified_time=now,
            etag='"b"',
        ),
    )
    return cat


def _add_policy(
    catalog: MemoryCatalogRepository,
    *,
    policy_id: str,
    filter_predicate: str,
    grantees: tuple[str, ...],
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    catalog.create_row_access_policy(
        RowAccessPolicyMeta(
            project_id="p",
            dataset_id="ds",
            table_id="orders",
            policy_id=policy_id,
            filter_predicate=filter_predicate,
            grantees=grantees,
            creation_time=now,
            last_modified_time=now,
            etag=f'"{policy_id}"',
        ),
    )


class TestShortCircuit:
    def test_no_policies_no_view_returns_input_unchanged(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        caller = CallerIdentity(principal="user:a@x", is_authenticated=True)
        sql = "SELECT * FROM ds.orders"
        out = rewrite_for_row_access(
            sql,
            project_id="p",
            caller=caller,
            catalog=catalog,
        )
        # Plain table reference with no policies: rewriter passes through.
        assert out == sql

    def test_unparseable_sql_returned_unchanged(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        # A garbage string SQLGlot can't parse — rewriter shouldn't crash.
        sql = "this is not :: sql"
        out = rewrite_for_row_access(
            sql,
            project_id="p",
            caller=CallerIdentity(principal="user:a@x"),
            catalog=catalog,
        )
        assert out == sql

    def test_view_expanded_even_without_policies(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        # A regular VIEW (no policies anywhere) should still expand
        # inline so the underlying base table is what DuckDB sees.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="v",
                table_type="VIEW",
                view_query="SELECT * FROM ds.orders",
                creation_time=now,
                last_modified_time=now,
                etag='"v"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.v",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "ds.orders" in out


class TestLeafTableEnforcement:
    def test_caller_with_grant_gets_filter(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:eu@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "region = 'EU'" in out
        assert "WHERE FALSE" not in out

    def test_caller_without_grant_gets_where_false(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "WHERE FALSE" in out

    def test_anonymous_caller_blocked(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="auth",
            filter_predicate="1=1",
            grantees=("allAuthenticatedUsers",),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.orders",
            project_id="p",
            caller=CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False),
            catalog=catalog,
        )
        assert "WHERE FALSE" in out

    def test_or_combines_multiple_matching_policies(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        _add_policy(
            catalog,
            policy_id="vip",
            filter_predicate="vip = TRUE",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:eu@x", is_authenticated=True),
            catalog=catalog,
        )
        # Both predicates should be OR-combined.
        assert "region = 'EU'" in out
        assert "vip = TRUE" in out
        assert " OR " in out.upper()

    def test_unrelated_table_unaffected(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        # Add a policy on `orders` only.
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        # Add an unrelated table to the same dataset.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="public",
                creation_time=now,
                last_modified_time=now,
                etag='"pub"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.public",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        # The other-table query should be unchanged (no policies on it).
        assert "WHERE" not in out.upper()

    def test_alias_preserved(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="all",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        out = rewrite_for_row_access(
            "SELECT o.id FROM ds.orders AS o",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "AS o" in out

    def test_reserved_schema_skipped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        # A query targeting the reserved snapshot schema should pass
        # through; the time-travel rewriter handles those.
        sql = 'SELECT * FROM "_bqemulator_snapshots"."s_1"'
        out = rewrite_for_row_access(
            sql,
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "_bqemulator_snapshots" in out


class TestViewExpansion:
    def test_authorized_view_still_enforces_rap(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        """RAP applies even when the querying view is authorized on the
        base dataset — BigQuery does not bypass row-level security for
        authorized views (ADR 0018 revised 2026-05-18, empirically
        confirmed by the 5 ``authz_view_*`` conformance fixtures whose
        cross-dataset re-records all returned 0 rows from real BQ)."""
        # Set up a protected base table.
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        # Authorize the view's dataset on the base table's dataset.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        ds = catalog.get_dataset("p", "ds")
        assert ds is not None
        catalog.update_dataset(
            ds.model_copy(
                update={
                    "access_entries": (AccessEntry(view=("p", "view_ds", "v")),),
                },
            ),
        )
        # Create the view.
        catalog.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="view_ds",
                creation_time=now,
                last_modified_time=now,
                etag='"vds"',
            ),
        )
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="view_ds",
                table_id="v",
                table_type="VIEW",
                view_query="SELECT * FROM ds.orders",
                creation_time=now,
                last_modified_time=now,
                etag='"v"',
            ),
        )
        # Caller without a grant queries the view: RAP still applies to
        # the base table, even though the view's dataset is authorized.
        out = rewrite_for_row_access(
            "SELECT * FROM view_ds.v",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "WHERE FALSE" in out

    def test_unauthorized_view_does_not_bypass(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="vds",
                creation_time=now,
                last_modified_time=now,
                etag='"vds"',
            ),
        )
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="vds",
                table_id="v",
                table_type="VIEW",
                view_query="SELECT * FROM ds.orders",
                creation_time=now,
                last_modified_time=now,
                etag='"v"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM vds.v",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x", is_authenticated=True),
            catalog=catalog,
        )
        assert "WHERE FALSE" in out

    def test_unauthorized_view_propagates_matching_filter(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        """RAP filter applies through an unauthorized view to its base table.

        Regression guard for the ``rap_filter_via_view`` conformance
        fixture (closed 2026-05-19). Pre-fix, SQL-created views were
        never synced into the catalog with ``table_type='VIEW'`` +
        ``view_query``, so the rewriter's ``_expand_view`` branch never
        fired for a SQL-created view; DuckDB then expanded the view
        internally and read the base table unfiltered. After the
        ``sync_created_view`` helper landed, the rewriter walks the
        view body and applies the caller-bound RAP filter to every
        base-table reference inside.
        """
        # Policy: country = 'US' for the caller's principal.
        _add_policy(
            catalog,
            policy_id="us_only",
            filter_predicate="country = 'US'",
            grantees=("user:eu@x",),
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="orders_view",
                table_type="VIEW",
                view_query="SELECT id, country, amount FROM `p.ds.orders`",
                creation_time=now,
                last_modified_time=now,
                etag='"ov"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT id, country, amount FROM `p.ds.orders_view` ORDER BY id",
            project_id="p",
            caller=CallerIdentity(principal="user:eu@x", is_authenticated=True),
            catalog=catalog,
        )
        # The view body was expanded into a derived subquery AND the
        # RAP filter was applied to the inner ``orders`` reference.
        assert "country = 'US'" in out
        assert "WHERE FALSE" not in out
        # The base-table reference inside the view body got wrapped.
        assert "orders" in out

    def test_view_with_unparseable_body_left_alone(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="vds",
                creation_time=now,
                last_modified_time=now,
                etag='"a"',
            ),
        )
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="vds",
                table_id="bad",
                table_type="VIEW",
                view_query="this is not :: SQL",
                creation_time=now,
                last_modified_time=now,
                etag='"bad"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM vds.bad",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        # No exception; the view stays as a regular table reference.
        assert "vds" in out


class TestOtherShapes:
    def test_join_protected_with_unprotected(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="all",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="public",
                creation_time=now,
                last_modified_time=now,
                etag='"pub"',
            ),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM ds.orders o JOIN ds.public p ON o.id = p.id",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        # The protected table is wrapped, the public table is not.
        assert "(SELECT * FROM ds.orders WHERE (1 = 1))" in out
        assert "ds.public AS p" in out or "ds.public p" in out

    def test_three_part_qualified_name(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="all",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        out = rewrite_for_row_access(
            "SELECT * FROM p.ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:a@x", is_authenticated=True),
            catalog=catalog,
        )
        # The fully-qualified ref should be wrapped just like ds.orders.
        assert "(1 = 1)" in out


class TestDmlTargets:
    """DML *write* targets must never be wrapped — RAP is read-only."""

    def test_insert_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        out = rewrite_for_row_access(
            "INSERT INTO ds.orders VALUES (1, 'EU')",
            project_id="p",
            caller=CallerIdentity(
                principal="user:other@x",
                is_authenticated=True,
            ),
            catalog=catalog,
        )
        assert "INSERT INTO ds.orders" in out

    def test_insert_target_unwrapped_when_caller_blocked(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "INSERT INTO ds.orders (id, region) VALUES (5, 'US')",
            project_id="p",
            caller=CallerIdentity(
                principal=DEFAULT_CALLER,
                is_authenticated=False,
            ),
            catalog=catalog,
        )
        assert "INSERT INTO ds.orders" in out
        assert "WHERE FALSE" not in out

    def test_update_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        out = rewrite_for_row_access(
            "UPDATE ds.orders SET region = 'EU' WHERE id = 1",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        # The UPDATE target stays unwrapped; the rewriter never wraps
        # write targets even if the caller doesn't match the policy.
        assert "UPDATE ds.orders" in out

    def test_delete_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="x",
            filter_predicate="1=1",
            grantees=("allUsers",),
        )
        out = rewrite_for_row_access(
            "DELETE FROM ds.orders WHERE id = 1",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        assert "DELETE FROM ds.orders" in out


class TestDDLTargetsUnwrapped:
    """``CREATE TABLE foo (...)`` names a NEW table — RAP must not engage.

    Regression for the bug where a stale RAP on ``ds.orders`` caused
    a subsequent ``CREATE TABLE ds.orders (...)`` (e.g. a re-creation
    after teardown failed to cascade RAP cleanup) to be rewritten into
    ``CREATE TABLE (SELECT * FROM ds.orders WHERE FALSE) AS orders (...)``,
    which then failed to parse. Discovered during P1 closure smoke-testing
    of the Java E2E suite (`@BeforeEach` runs the CREATE TABLE 4 times).
    """

    def test_create_table_with_schema_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "CREATE TABLE ds.orders (id INT64, region STRING)",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        assert "CREATE TABLE ds.orders (" in out
        assert "WHERE FALSE" not in out

    def test_create_or_replace_table_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        _add_policy(
            catalog,
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "CREATE OR REPLACE TABLE ds.orders (id INT64)",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        assert "ds.orders" in out
        assert "WHERE FALSE" not in out

    def test_create_table_as_select_walks_inner_query(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        """The destination is unwrapped, but the SELECT body still gets RAP."""
        _add_policy(
            catalog,
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "CREATE TABLE ds.copy AS SELECT * FROM ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        # The DDL target ``ds.copy`` stays unwrapped.
        assert "CREATE TABLE ds.copy AS" in out
        # The inner read of ``ds.orders`` IS subject to RAP (denied here).
        assert "WHERE FALSE" in out

    def test_drop_view_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        """``DROP VIEW`` must not trigger view-expansion of the target.

        Regression for the 2026-05-19 closure: the new
        ``sync_created_view`` helper populates the catalog with
        ``table_type='VIEW'`` for SQL-created views. Without the DDL-
        target carve-out, the rewriter would replace the ``DROP VIEW
        v_active`` target with a derived ``(SELECT … FROM base) AS
        v_active`` subquery, yielding invalid SQL
        (``DROP VIEW (SELECT …)``) that fails to parse.
        """
        now = datetime(2026, 5, 19, tzinfo=UTC)
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="v_active",
                table_type="VIEW",
                view_query="SELECT * FROM ds.orders",
                creation_time=now,
                last_modified_time=now,
                etag='"v"',
            ),
        )
        out = rewrite_for_row_access(
            "DROP VIEW ds.v_active",
            project_id="p",
            caller=CallerIdentity(principal="user:any@x"),
            catalog=catalog,
        )
        # ``DROP VIEW`` target stays as a bare table reference.
        assert "DROP VIEW" in out.upper()
        assert "SELECT" not in out.upper()
        assert "v_active" in out

    def test_drop_table_target_unwrapped(
        self,
        catalog: MemoryCatalogRepository,
    ) -> None:
        """``DROP TABLE`` is also a DDL target — RAP must not engage."""
        _add_policy(
            catalog,
            policy_id="eu_only",
            filter_predicate="region = 'EU'",
            grantees=("user:eu@x",),
        )
        out = rewrite_for_row_access(
            "DROP TABLE ds.orders",
            project_id="p",
            caller=CallerIdentity(principal="user:other@x"),
            catalog=catalog,
        )
        assert "DROP TABLE" in out.upper()
        assert "WHERE FALSE" not in out
