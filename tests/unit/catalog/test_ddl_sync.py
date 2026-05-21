"""Tests for the CREATE TABLE → catalog auto-sync helper (ADR 0023 §1.F)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.ddl_sync import sync_created_table, sync_created_view
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 16, tzinfo=UTC)


@pytest_asyncio.fixture
async def ctx(ephemeral_settings: Settings) -> AsyncIterator[AppContext]:
    """In-process ``AppContext`` with one dataset ``p.ds`` registered."""
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    context = AppContext(
        settings=ephemeral_settings,
        clock=FrozenClock(NOW),
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=EventBus(),
        udf_registry=UDFRegistry(ephemeral_settings),
        snapshots=SnapshotManager(
            engine=engine,
            catalog=MemoryCatalogRepository(),
            clock=FrozenClock(),
            events=EventBus(),
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock()),
    )
    try:
        yield context
    finally:
        await engine.stop()


class TestSyncCreatedTable:
    """``sync_created_table`` mirrors DDL-created tables into the catalog."""

    async def test_create_table_as_select_registers(self, ctx: AppContext) -> None:
        """``CREATE TABLE foo AS SELECT …`` registers ``foo`` with schema."""
        ctx.engine.execute(
            'CREATE OR REPLACE TABLE "p__ds"."t1" AS SELECT 1 AS id, \'a\' AS label',
        )
        sync_created_table(
            "CREATE OR REPLACE TABLE `p.ds.t1` AS SELECT 1 AS id, 'a' AS label",
            "p",
            ctx,
        )
        meta = ctx.catalog.get_table("p", "ds", "t1")
        assert meta is not None
        assert meta.table_type == "TABLE"
        field_names = [f.name for f in meta.schema_.fields]
        field_types = [f.type for f in meta.schema_.fields]
        assert field_names == ["id", "label"]
        assert field_types == ["INTEGER", "STRING"]
        assert meta.num_rows == 1

    async def test_create_table_with_column_list_registers(self, ctx: AppContext) -> None:
        """``CREATE TABLE foo (cols …)`` registers an empty table."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t_empty" (id BIGINT, label VARCHAR)')
        sync_created_table(
            "CREATE TABLE `p.ds.t_empty` (id INT64, label STRING)",
            "p",
            ctx,
        )
        meta = ctx.catalog.get_table("p", "ds", "t_empty")
        assert meta is not None
        assert meta.num_rows == 0
        assert [f.name for f in meta.schema_.fields] == ["id", "label"]

    async def test_create_or_replace_updates_existing_entry(
        self,
        ctx: AppContext,
    ) -> None:
        """A second ``CREATE OR REPLACE`` refreshes the schema in place."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t_repl" AS SELECT 1 AS id')
        sync_created_table("CREATE TABLE `p.ds.t_repl` AS SELECT 1 AS id", "p", ctx)
        first = ctx.catalog.get_table("p", "ds", "t_repl")
        assert first is not None
        assert [f.name for f in first.schema_.fields] == ["id"]

        ctx.engine.execute(
            'CREATE OR REPLACE TABLE "p__ds"."t_repl" AS SELECT 1 AS id, \'x\' AS label',
        )
        sync_created_table(
            "CREATE OR REPLACE TABLE `p.ds.t_repl` AS SELECT 1 AS id, 'x' AS label",
            "p",
            ctx,
        )
        second = ctx.catalog.get_table("p", "ds", "t_repl")
        assert second is not None
        assert [f.name for f in second.schema_.fields] == ["id", "label"]

    async def test_create_view_does_not_register_table(self, ctx: AppContext) -> None:
        """``CREATE VIEW`` is handled elsewhere — not by this sync helper."""
        sync_created_table(
            "CREATE OR REPLACE VIEW `p.ds.v1` AS SELECT 1",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "v1") is None

    async def test_create_materialized_view_does_not_register_table(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE MATERIALIZED VIEW`` is routed via the MV manager."""
        sync_created_table(
            "CREATE MATERIALIZED VIEW `p.ds.mv1` AS SELECT 1",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "mv1") is None

    async def test_create_table_clone_does_not_register_via_sync(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE TABLE … CLONE`` is routed via the clone manager."""
        sync_created_table(
            "CREATE OR REPLACE TABLE `p.ds.cloned` CLONE `p.ds.src`",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "cloned") is None

    async def test_select_only_is_noop(self, ctx: AppContext) -> None:
        """A SELECT is not a CREATE — sync ignores it."""
        sync_created_table("SELECT 1", "p", ctx)
        assert ctx.catalog.list_tables("p", "ds") == ()

    async def test_dml_is_noop(self, ctx: AppContext) -> None:
        """INSERT / UPDATE / DELETE shapes ignored."""
        sync_created_table(
            "INSERT INTO `p.ds.t1` (id) VALUES (1)",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "t1") is None

    async def test_unparseable_sql_is_noop(self, ctx: AppContext) -> None:
        """SQLGlot parse failures must not crash the helper."""
        # ``CREATE SNAPSHOT TABLE`` falls back to ``Command`` — not a
        # plain CREATE TABLE — so the sync helper skips it without
        # raising.
        sync_created_table(
            "CREATE SNAPSHOT TABLE `p.ds.snap` CLONE `p.ds.src`",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "snap") is None

    async def test_missing_dataset_is_silent_noop(self, ctx: AppContext) -> None:
        """No dataset = no registration — DDL still ran in DuckDB."""
        sync_created_table(
            "CREATE TABLE `p.absent.t` AS SELECT 1",
            "p",
            ctx,
        )
        # No table was added because the dataset doesn't exist.
        assert ctx.catalog.get_table("p", "absent", "t") is None


class TestSyncCreatedView:
    """``sync_created_view`` mirrors DDL-created views into the catalog.

    Closes the ``rap_filter_via_view`` conformance fixture: the
    row-access rewriter's ``_expand_view`` branch only fires when a
    referenced table has ``table_type='VIEW'`` + ``view_query``. Prior
    to this helper, SQL-created views never reached that branch and
    DuckDB expanded them internally with no RAP filtering.
    """

    async def test_create_view_registers_with_view_query(self, ctx: AppContext) -> None:
        """``CREATE VIEW`` registers ``table_type='VIEW'`` with ``view_query``."""
        # The view must exist in DuckDB for schema introspection to
        # succeed; that's what the executor / interpreter would do before
        # invoking this helper.
        ctx.engine.execute('CREATE TABLE "p__ds"."orders" (id BIGINT, country VARCHAR)')
        ctx.engine.execute(
            'CREATE OR REPLACE VIEW "p__ds"."orders_view" AS '
            'SELECT id, country FROM "p__ds"."orders"',
        )
        sync_created_view(
            "CREATE OR REPLACE VIEW `p.ds.orders_view` AS SELECT id, country FROM `p.ds.orders`",
            "p",
            ctx,
        )
        meta = ctx.catalog.get_table("p", "ds", "orders_view")
        assert meta is not None
        assert meta.table_type == "VIEW"
        assert meta.view_query is not None
        assert "SELECT" in meta.view_query.upper()
        assert "orders" in meta.view_query
        assert [f.name for f in meta.schema_.fields] == ["id", "country"]
        # Views have no physical rows; ``num_rows`` must stay 0 so a
        # downstream consumer doesn't ``SELECT COUNT(*)`` the view body.
        assert meta.num_rows == 0

    async def test_create_or_replace_updates_existing_view(self, ctx: AppContext) -> None:
        """A second ``CREATE OR REPLACE VIEW`` refreshes the body in place."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (a BIGINT, b BIGINT)')
        ctx.engine.execute('CREATE OR REPLACE VIEW "p__ds"."v" AS SELECT a FROM "p__ds"."t"')
        sync_created_view(
            "CREATE OR REPLACE VIEW `p.ds.v` AS SELECT a FROM `p.ds.t`",
            "p",
            ctx,
        )
        first = ctx.catalog.get_table("p", "ds", "v")
        assert first is not None
        assert [f.name for f in first.schema_.fields] == ["a"]

        ctx.engine.execute(
            'CREATE OR REPLACE VIEW "p__ds"."v" AS SELECT a, b FROM "p__ds"."t"',
        )
        sync_created_view(
            "CREATE OR REPLACE VIEW `p.ds.v` AS SELECT a, b FROM `p.ds.t`",
            "p",
            ctx,
        )
        second = ctx.catalog.get_table("p", "ds", "v")
        assert second is not None
        assert [f.name for f in second.schema_.fields] == ["a", "b"]

    async def test_create_table_is_noop(self, ctx: AppContext) -> None:
        """``CREATE TABLE`` is handled by ``sync_created_table`` — not this helper."""
        sync_created_view(
            "CREATE OR REPLACE TABLE `p.ds.t` AS SELECT 1 AS id",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "t") is None

    async def test_create_materialized_view_is_noop(self, ctx: AppContext) -> None:
        """``CREATE MATERIALIZED VIEW`` is routed via the MV manager."""
        sync_created_view(
            "CREATE MATERIALIZED VIEW `p.ds.mv` AS SELECT 1 AS id",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "mv") is None

    async def test_select_only_is_noop(self, ctx: AppContext) -> None:
        """A SELECT is not a CREATE VIEW — sync ignores it."""
        sync_created_view("SELECT 1", "p", ctx)
        assert ctx.catalog.list_tables("p", "ds") == ()

    async def test_unparseable_sql_is_noop(self, ctx: AppContext) -> None:
        """SQLGlot parse failures must not crash the helper."""
        sync_created_view("garbage :: not :: sql", "p", ctx)
        assert ctx.catalog.list_tables("p", "ds") == ()

    async def test_missing_dataset_is_silent_noop(self, ctx: AppContext) -> None:
        """No dataset = no registration — DDL still ran in DuckDB."""
        sync_created_view(
            "CREATE VIEW `p.absent.v` AS SELECT 1",
            "p",
            ctx,
        )
        assert ctx.catalog.get_table("p", "absent", "v") is None
