"""Tests for the SQL-DDL → catalog auto-sync helpers (ADR 0023 §1.F).

Covers both the CREATE side (``sync_created_{table,view,schema}``) and
the DROP side (``sync_dropped_object``) that reconciles the catalog
after a ``DROP TABLE/VIEW/SCHEMA``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.ddl_sync import (
    _detect_catalog_drop,
    _detect_create_schema,
    assert_drop_schema_allowed,
    sync_created_schema,
    sync_created_table,
    sync_created_view,
    sync_dropped_object,
)
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import NotFoundError, ResourceInUseError
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import execute_query_job
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

    async def test_missing_dataset_is_auto_registered(self, ctx: AppContext) -> None:
        """An unregistered dataset is auto-registered alongside the table.

        Real BigQuery makes a table created via ``CREATE SCHEMA`` +
        ``CREATE TABLE`` visible through INFORMATION_SCHEMA and the REST
        API. The sync helper registers the missing dataset rather than
        silently skipping — the old skip left the table catalog-invisible,
        which broke row-access-policy target validation
        (``Not found: table`` despite the table living in DuckDB).
        """
        ctx.engine.execute('CREATE SCHEMA IF NOT EXISTS "p__absent"')
        ctx.engine.execute('CREATE TABLE "p__absent"."t" AS SELECT 1 AS id')
        sync_created_table("CREATE TABLE `p.absent.t` AS SELECT 1 AS id", "p", ctx)
        assert ctx.catalog.get_dataset("p", "absent") is not None
        meta = ctx.catalog.get_table("p", "absent", "t")
        assert meta is not None
        assert meta.table_type == "TABLE"
        assert [f.name for f in meta.schema_.fields] == ["id"]


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

    async def test_missing_dataset_is_auto_registered(self, ctx: AppContext) -> None:
        """An unregistered dataset is auto-registered alongside the view."""
        ctx.engine.execute('CREATE SCHEMA IF NOT EXISTS "p__absent"')
        ctx.engine.execute('CREATE VIEW "p__absent"."v" AS SELECT 1 AS id')
        sync_created_view("CREATE VIEW `p.absent.v` AS SELECT 1 AS id", "p", ctx)
        assert ctx.catalog.get_dataset("p", "absent") is not None
        meta = ctx.catalog.get_table("p", "absent", "v")
        assert meta is not None
        assert meta.table_type == "VIEW"


class TestSyncCreatedSchema:
    """``sync_created_schema`` registers SQL ``CREATE SCHEMA`` datasets."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("CREATE SCHEMA newds", ("p", "newds")),
            ("CREATE SCHEMA IF NOT EXISTS newds", ("p", "newds")),
            ("CREATE SCHEMA otherproj.newds", ("otherproj", "newds")),
            ("CREATE SCHEMA `otherproj`.`newds`", ("otherproj", "newds")),
            ("CREATE SCHEMA `otherproj.newds`", ("otherproj", "newds")),
        ],
    )
    def test_detect_create_schema(self, sql: str, expected: tuple[str, str]) -> None:
        assert _detect_create_schema(sql, "p") == expected

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TABLE t (id INT64)",
            "SELECT 1",
            "DROP SCHEMA ds",
            "this is not sql",
        ],
    )
    def test_detect_create_schema_non_schema_returns_none(self, sql: str) -> None:
        assert _detect_create_schema(sql, "p") is None

    async def test_sync_registers_new_dataset(self, ctx: AppContext) -> None:
        """``CREATE SCHEMA`` registers a catalog dataset."""
        assert ctx.catalog.get_dataset("p", "newds") is None
        sync_created_schema("CREATE SCHEMA newds", "p", ctx)
        assert ctx.catalog.get_dataset("p", "newds") is not None

    async def test_sync_is_idempotent_on_existing_dataset(self, ctx: AppContext) -> None:
        """An already-registered dataset is left untouched (no duplicate)."""
        before = ctx.catalog.get_dataset("p", "ds")
        assert before is not None
        sync_created_schema("CREATE SCHEMA IF NOT EXISTS ds", "p", ctx)
        after = ctx.catalog.get_dataset("p", "ds")
        assert after is not None
        # Same etag — the existing entry was not replaced.
        assert after.etag == before.etag

    async def test_sync_non_schema_is_noop(self, ctx: AppContext) -> None:
        sync_created_schema("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        assert ctx.catalog.list_datasets("p") == (ctx.catalog.get_dataset("p", "ds"),)


class TestExtractDdlMetadata:
    """``_extract_ddl_metadata`` parses CREATE TABLE for description + partitioning."""

    def test_no_properties_returns_empty(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata("CREATE TABLE t (id INT64)")
        assert extras.description is None
        assert extras.time_partitioning is None

    def test_unparseable_sql_returns_empty(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata("CREATE TABLE ((unbalanced")
        assert extras.description is None
        assert extras.time_partitioning is None

    def test_non_create_statement_returns_empty(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata("SELECT 1")
        assert extras.description is None
        assert extras.time_partitioning is None

    def test_partition_by_column_extracted(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata("CREATE TABLE t (dt DATE, v INT64) PARTITION BY dt")
        assert extras.time_partitioning is not None
        assert extras.time_partitioning.field == "dt"
        assert extras.time_partitioning.type == "DAY"
        assert extras.time_partitioning.require_partition_filter is False

    def test_ingestion_time_pseudo_columns_yield_no_field(self) -> None:
        """`_PARTITIONDATE` / `_PARTITIONTIME` are ingestion-time markers; field stays None."""
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        for col in ("_PARTITIONDATE", "_PARTITIONTIME"):
            extras = _extract_ddl_metadata(f"CREATE TABLE t (v INT64) PARTITION BY {col}")
            # No real-column partition → no TimePartitioning constructed when
            # no other partitioning option is set.
            assert extras.time_partitioning is None, col

    def test_description_option_extracted(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata('CREATE TABLE t (id INT64) OPTIONS(description="hello")')
        assert extras.description == "hello"

    def test_require_partition_filter_option(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata(
            "CREATE TABLE t (dt DATE, v INT64) "
            "PARTITION BY dt "
            "OPTIONS(require_partition_filter=TRUE)"
        )
        assert extras.time_partitioning is not None
        assert extras.time_partitioning.require_partition_filter is True

    def test_partition_expiration_days_option(self) -> None:
        from bqemulator.catalog.ddl_sync import _extract_ddl_metadata

        extras = _extract_ddl_metadata(
            "CREATE TABLE t (dt DATE, v INT64) "
            "PARTITION BY dt "
            "OPTIONS(partition_expiration_days=30)"
        )
        assert extras.time_partitioning is not None
        # 30 days in ms.
        assert extras.time_partitioning.expiration_ms == 30 * 24 * 60 * 60 * 1000

    def test_partition_expiration_days_invalid_literal(self) -> None:
        from bqemulator.catalog.ddl_sync import _days_literal_to_ms

        assert _days_literal_to_ms(None) is None
        assert _days_literal_to_ms("not-a-number") is None
        assert _days_literal_to_ms(7) == 7 * 24 * 60 * 60 * 1000
        assert _days_literal_to_ms("7") == 7 * 24 * 60 * 60 * 1000

    def test_build_time_partitioning_returns_none_when_all_unset(self) -> None:
        from bqemulator.catalog.ddl_sync import _build_time_partitioning, _DdlOptions

        assert _build_time_partitioning(None, _DdlOptions()) is None

    def test_build_time_partitioning_with_require_filter_only(self) -> None:
        """``require_partition_filter=TRUE`` on its own still synthesises TimePartitioning."""
        from bqemulator.catalog.ddl_sync import _build_time_partitioning, _DdlOptions

        tp = _build_time_partitioning(None, _DdlOptions(require_partition_filter=True))
        assert tp is not None
        assert tp.field is None
        assert tp.require_partition_filter is True


class TestArrowFieldToTableField:
    """``_arrow_field_to_table_field`` recursively maps Arrow types to TableFieldSchema."""

    def test_scalar_int(self) -> None:
        import pyarrow as pa

        from bqemulator.catalog.ddl_sync import _arrow_field_to_table_field

        field = pa.field("id", pa.int64())
        out = _arrow_field_to_table_field(field)
        assert out.name == "id"
        assert out.type == "INTEGER"
        assert out.mode == "NULLABLE"
        assert out.fields == ()

    def test_mode_override_required(self) -> None:
        import pyarrow as pa

        from bqemulator.catalog.ddl_sync import _arrow_field_to_table_field

        out = _arrow_field_to_table_field(pa.field("id", pa.int64()), mode_override="REQUIRED")
        assert out.mode == "REQUIRED"

    def test_list_becomes_repeated(self) -> None:
        import pyarrow as pa

        from bqemulator.catalog.ddl_sync import _arrow_field_to_table_field

        field = pa.field("tags", pa.list_(pa.string()))
        out = _arrow_field_to_table_field(field)
        assert out.name == "tags"
        assert out.mode == "REPEATED"
        assert out.type == "STRING"

    def test_struct_becomes_record_with_nested_fields(self) -> None:
        import pyarrow as pa

        from bqemulator.catalog.ddl_sync import _arrow_field_to_table_field

        field = pa.field(
            "address",
            pa.struct([("city", pa.string()), ("zip", pa.int64())]),
        )
        out = _arrow_field_to_table_field(field)
        assert out.name == "address"
        assert out.type == "RECORD"
        assert out.mode == "NULLABLE"
        assert len(out.fields) == 2
        assert out.fields[0].name == "city"
        assert out.fields[0].type == "STRING"
        assert out.fields[1].name == "zip"
        assert out.fields[1].type == "INTEGER"


class TestHasNotNullConstraint:
    """``_has_not_null_constraint`` reads the SQLGlot ColumnDef AST."""

    def test_column_with_not_null(self) -> None:
        import sqlglot
        from sqlglot import expressions as exp

        from bqemulator.api.routes.jobs import _has_not_null_constraint

        tree = sqlglot.parse_one("CREATE TABLE t (id INT64 NOT NULL)", read="bigquery")
        column = next(c for c in tree.this.expressions if isinstance(c, exp.ColumnDef))
        assert _has_not_null_constraint(column) is True

    def test_column_without_not_null(self) -> None:
        import sqlglot
        from sqlglot import expressions as exp

        from bqemulator.api.routes.jobs import _has_not_null_constraint

        tree = sqlglot.parse_one("CREATE TABLE t (id INT64)", read="bigquery")
        column = next(c for c in tree.this.expressions if isinstance(c, exp.ColumnDef))
        assert _has_not_null_constraint(column) is False


class TestDetectCatalogDrop:
    """``_detect_catalog_drop`` recognises only catalog-tracked DROP forms."""

    @pytest.mark.parametrize(
        ("sql", "kind"),
        [
            ("DROP TABLE `p.ds.t`", "TABLE"),
            ("DROP TABLE IF EXISTS `ds.t`", "TABLE"),
            ("DROP VIEW `ds.v`", "VIEW"),
            ("DROP VIEW IF EXISTS `ds.v`", "VIEW"),
            ("DROP SCHEMA `ds`", "SCHEMA"),
            ("DROP SCHEMA IF EXISTS `p.ds` CASCADE", "SCHEMA"),
        ],
    )
    def test_detects_tracked_drops(self, sql: str, kind: str) -> None:
        drop = _detect_catalog_drop(sql)
        assert drop is not None
        assert (drop.args.get("kind") or "").upper() == kind

    def test_cascade_flag_parsed(self) -> None:
        """``CASCADE`` sets the ``cascade`` arg; bare / RESTRICT leaves it false."""
        cascade = _detect_catalog_drop("DROP SCHEMA `ds` CASCADE")
        plain = _detect_catalog_drop("DROP SCHEMA `ds`")
        assert cascade is not None
        assert plain is not None
        assert cascade.args.get("cascade") is True
        assert plain.args.get("cascade") is False

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP MATERIALIZED VIEW `ds.mv`",  # routed via the versioning DDL manager
            "DROP SNAPSHOT TABLE `ds.s`",  # parses as Command, not Drop
            "DROP EXTERNAL TABLE `ds.e`",  # parses as Command, not Drop
            "DROP FUNCTION `ds.fn`",  # untracked kind
            "DROP PROCEDURE `ds.proc`",  # untracked kind
            "SELECT 1",
            "CREATE TABLE `p.ds.t` (id INT64)",
            "INSERT INTO `p.ds.t` (id) VALUES (1)",
            "garbage :: not :: sql",
        ],
    )
    def test_ignores_untracked_or_non_drop(self, sql: str) -> None:
        assert _detect_catalog_drop(sql) is None


class TestSyncDroppedObject:
    """``sync_dropped_object`` removes catalog metadata after a successful DROP."""

    async def test_drop_table_removes_catalog_entry(self, ctx: AppContext) -> None:
        """``DROP TABLE`` removes the ``TableMeta`` but keeps the dataset."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is not None
        sync_dropped_object("DROP TABLE `p.ds.t`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is None
        assert ctx.catalog.get_dataset("p", "ds") is not None

    async def test_drop_table_if_exists_qualified_name(self, ctx: AppContext) -> None:
        """A project-qualified ``DROP TABLE IF EXISTS`` resolves and removes the entry."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        sync_dropped_object("DROP TABLE IF EXISTS `p.ds.t`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is None

    async def test_drop_view_removes_catalog_entry(self, ctx: AppContext) -> None:
        """``DROP VIEW`` removes the view entry and leaves the base table."""
        ctx.engine.execute('CREATE TABLE "p__ds"."base" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.base` (id INT64)", "p", ctx)
        ctx.engine.execute('CREATE VIEW "p__ds"."v" AS SELECT id FROM "p__ds"."base"')
        sync_created_view("CREATE VIEW `p.ds.v` AS SELECT id FROM `p.ds.base`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "v") is not None
        sync_dropped_object("DROP VIEW `p.ds.v`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "v") is None
        assert ctx.catalog.get_table("p", "ds", "base") is not None

    async def test_drop_only_targets_named_relation(self, ctx: AppContext) -> None:
        """A drop removes only its target; sibling tables survive."""
        for name in ("keep", "remove"):
            ctx.engine.execute(f'CREATE TABLE "p__ds"."{name}" (id BIGINT)')
            sync_created_table(f"CREATE TABLE `p.ds.{name}` (id INT64)", "p", ctx)
        sync_dropped_object("DROP TABLE `p.ds.remove`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "remove") is None
        assert ctx.catalog.get_table("p", "ds", "keep") is not None

    async def test_drop_absent_table_is_idempotent(self, ctx: AppContext) -> None:
        """``not_found_ok`` means dropping an unregistered relation never raises."""
        sync_dropped_object("DROP TABLE IF EXISTS `p.ds.ghost`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "ghost") is None

    async def test_drop_schema_removes_empty_dataset(self, ctx: AppContext) -> None:
        """``DROP SCHEMA`` removes an empty dataset from the catalog."""
        assert ctx.catalog.get_dataset("p", "ds") is not None
        sync_dropped_object("DROP SCHEMA `ds`", "p", ctx)
        assert ctx.catalog.get_dataset("p", "ds") is None

    async def test_drop_schema_cascade_removes_dataset_and_tables(
        self,
        ctx: AppContext,
    ) -> None:
        """``DROP SCHEMA … CASCADE`` cascades the catalog removal to the tables."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        sync_dropped_object("DROP SCHEMA `ds` CASCADE", "p", ctx)
        assert ctx.catalog.get_dataset("p", "ds") is None
        assert ctx.catalog.get_table("p", "ds", "t") is None

    async def test_drop_schema_no_cascade_on_nonempty_respects_restrict(
        self,
        ctx: AppContext,
    ) -> None:
        """A non-empty dataset can't be dropped without CASCADE (RESTRICT default).

        In the real flow DuckDB raises first; the catalog guard enforces
        the same contract when the helper is exercised in isolation.
        """
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        with pytest.raises(NotFoundError):
            sync_dropped_object("DROP SCHEMA `ds`", "p", ctx)
        assert ctx.catalog.get_dataset("p", "ds") is not None

    async def test_drop_qualified_schema_resolves_project(self, ctx: AppContext) -> None:
        """A ``proj.dataset`` schema target resolves to the right dataset."""
        sync_dropped_object("DROP SCHEMA `p.ds`", "p", ctx)
        assert ctx.catalog.get_dataset("p", "ds") is None

    async def test_drop_materialized_view_is_skipped(self, ctx: AppContext) -> None:
        """``DROP MATERIALIZED VIEW`` is left to the versioning DDL manager."""
        ctx.engine.execute('CREATE TABLE "p__ds"."mv" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.mv` (id INT64)", "p", ctx)
        sync_dropped_object("DROP MATERIALIZED VIEW `p.ds.mv`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "mv") is not None

    async def test_drop_snapshot_table_is_skipped(self, ctx: AppContext) -> None:
        """``DROP SNAPSHOT TABLE`` parses as Command and is left to versioning DDL."""
        ctx.engine.execute('CREATE TABLE "p__ds"."s" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.s` (id INT64)", "p", ctx)
        sync_dropped_object("DROP SNAPSHOT TABLE `p.ds.s`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "s") is not None

    async def test_non_drop_sql_is_noop(self, ctx: AppContext) -> None:
        """SELECT / CREATE / unparseable input leaves the catalog untouched."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        for sql in ("SELECT 1", "CREATE TABLE `p.ds.x` (id INT64)", "garbage (("):
            sync_dropped_object(sql, "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is not None

    async def test_drop_bare_table_without_dataset_is_noop(self, ctx: AppContext) -> None:
        """A dataset-less ``DROP TABLE t`` resolves no dataset and is a no-op."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        sync_dropped_object("DROP TABLE `t`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is not None

    async def test_drop_overqualified_schema_is_noop(self, ctx: AppContext) -> None:
        """A three-part ``DROP SCHEMA a.b.c`` resolves no dataset and is a no-op."""
        sync_dropped_object("DROP SCHEMA `a.b.c`", "p", ctx)
        assert ctx.catalog.get_dataset("p", "ds") is not None

    async def test_drop_with_unwrappable_target_is_noop(
        self,
        ctx: AppContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DROP whose target doesn't unwrap to a table is a defensive no-op."""
        ctx.engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
        sync_created_table("CREATE TABLE `p.ds.t` (id INT64)", "p", ctx)
        monkeypatch.setattr(
            "bqemulator.catalog.ddl_sync._unwrap_table_target",
            lambda _target: None,
        )
        sync_dropped_object("DROP TABLE `p.ds.t`", "p", ctx)
        assert ctx.catalog.get_table("p", "ds", "t") is not None


class TestDropSyncWiring:
    """DROP through ``execute_query_job`` reconciles the catalog end-to-end."""

    async def test_single_statement_drop_removes_catalog_entry(
        self,
        ctx: AppContext,
    ) -> None:
        """A single ``DROP TABLE`` job removes the table from the catalog."""
        await execute_query_job(
            "p",
            "job-create",
            "CREATE TABLE `p.ds.wired` (id INT64)",
            None,
            ctx,
        )
        assert ctx.catalog.get_table("p", "ds", "wired") is not None
        await execute_query_job("p", "job-drop", "DROP TABLE `p.ds.wired`", None, ctx)
        assert ctx.catalog.get_table("p", "ds", "wired") is None
        assert ctx.catalog.list_tables("p", "ds") == ()

    async def test_scripted_drop_removes_catalog_entry(self, ctx: AppContext) -> None:
        """A multi-statement script routes the DROP through the interpreter hook."""
        script = "CREATE TABLE `p.ds.scripted` (id INT64);\nDROP TABLE `p.ds.scripted`;"
        await execute_query_job("p", "job-script", script, None, ctx)
        assert ctx.catalog.get_table("p", "ds", "scripted") is None


class TestCreateSchemaSyncWiring:
    """CREATE SCHEMA through ``execute_query_job`` registers the dataset end-to-end."""

    async def test_single_statement_create_schema_registers_dataset(
        self,
        ctx: AppContext,
    ) -> None:
        """A single ``CREATE SCHEMA`` job registers the dataset via the executor path."""
        assert ctx.catalog.get_dataset("p", "solo_ds") is None
        await execute_query_job("p", "job-create-schema", "CREATE SCHEMA solo_ds", None, ctx)
        assert ctx.catalog.get_dataset("p", "solo_ds") is not None

    async def test_scripted_create_schema_registers_dataset(self, ctx: AppContext) -> None:
        """A multi-statement script routes ``CREATE SCHEMA`` through the interpreter hook.

        A single-statement ``CREATE SCHEMA`` takes the executor fast path
        (already synced); a script with two or more statements runs
        through :class:`ScriptInterpreter`, whose ``_exec_sql`` hook must
        call ``sync_created_schema`` so a dataset created purely inside
        the script is catalog-visible (``datasets.list`` /
        INFORMATION_SCHEMA.SCHEMATA), matching real BigQuery. The trailing
        ``SELECT`` is what tips the job past the single-statement fast
        path into the interpreter.
        """
        assert ctx.catalog.get_dataset("p", "scripted_ds") is None
        script = "CREATE SCHEMA scripted_ds;\nSELECT 1 AS n;"
        await execute_query_job("p", "job-script-schema", script, None, ctx)
        assert ctx.catalog.get_dataset("p", "scripted_ds") is not None

    async def test_scripted_create_schema_visible_in_information_schema(
        self,
        ctx: AppContext,
    ) -> None:
        """A schema created in a script is visible to a later SCHEMATA query.

        The interpreter registers the ``CREATE SCHEMA`` dataset, and the
        same script's INFORMATION_SCHEMA.SCHEMATA query — expanded from
        the catalog — then surfaces it. The ``SELECT`` is the script's
        last statement, so its single row is the script result (matching
        BigQuery).
        """
        from bqemulator.scripting.interpreter import run_script

        script = (
            "CREATE SCHEMA scripted_visible_ds;\n"
            "SELECT schema_name FROM `region-us.INFORMATION_SCHEMA.SCHEMATA` "
            "WHERE schema_name = 'scripted_visible_ds' ORDER BY schema_name"
        )
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.to_pylist() == [{"schema_name": "scripted_visible_ds"}]


class TestAssertDropSchemaAllowed:
    """RESTRICT guard: bare DROP SCHEMA on a non-empty dataset is rejected.

    Real BigQuery returns ``resourceInUse`` (HTTP 400) unless the caller
    uses ``DROP SCHEMA … CASCADE``. Pinned end-to-end by
    ``rest_crud/ddl_drop_schema_non_empty_restrict`` and
    ``ddl_drop_schema_cascade``.
    """

    async def test_non_empty_bare_drop_raises(self, ctx: AppContext) -> None:
        await execute_query_job("p", "c", "CREATE TABLE `p.ds.t` (id INT64)", None, ctx)
        with pytest.raises(ResourceInUseError) as excinfo:
            assert_drop_schema_allowed("DROP SCHEMA `p.ds`", "p", ctx)
        # BigQuery-shaped message; no internal ``p__ds`` schema name leaked.
        assert excinfo.value.message == "Dataset p:ds is still in use"
        assert excinfo.value.bq_reason == "resourceInUse"
        assert excinfo.value.http_status == 400

    async def test_non_empty_due_to_routine_raises(self, ctx: AppContext) -> None:
        await execute_query_job(
            "p",
            "cf",
            "CREATE FUNCTION `p.ds.f`(x INT64) RETURNS INT64 AS (x + 1)",
            None,
            ctx,
        )
        with pytest.raises(ResourceInUseError):
            assert_drop_schema_allowed("DROP SCHEMA `p.ds`", "p", ctx)

    async def test_cascade_on_non_empty_is_allowed(self, ctx: AppContext) -> None:
        await execute_query_job("p", "c", "CREATE TABLE `p.ds.t` (id INT64)", None, ctx)
        # CASCADE: no raise — the drop proceeds and cascades the contents.
        assert_drop_schema_allowed("DROP SCHEMA `p.ds` CASCADE", "p", ctx)

    async def test_empty_dataset_bare_drop_is_allowed(self, ctx: AppContext) -> None:
        assert_drop_schema_allowed("DROP SCHEMA `p.ds`", "p", ctx)

    async def test_missing_dataset_is_noop(self, ctx: AppContext) -> None:
        # IF EXISTS no-ops; a bare drop of a missing dataset 404s downstream.
        assert_drop_schema_allowed("DROP SCHEMA `p.absent`", "p", ctx)
        assert_drop_schema_allowed("DROP SCHEMA IF EXISTS `p.absent`", "p", ctx)

    async def test_non_schema_drop_is_noop(self, ctx: AppContext) -> None:
        await execute_query_job("p", "c", "CREATE TABLE `p.ds.t` (id INT64)", None, ctx)
        # DROP TABLE / non-DROP statements are not the guard's concern.
        assert_drop_schema_allowed("DROP TABLE `p.ds.t`", "p", ctx)
        assert_drop_schema_allowed("SELECT 1", "p", ctx)

    async def test_end_to_end_non_empty_drop_surfaces_resource_in_use(
        self,
        ctx: AppContext,
    ) -> None:
        """``execute_query_job`` raises ResourceInUseError; the dataset survives."""
        await execute_query_job("p", "c", "CREATE TABLE `p.ds.t` (id INT64)", None, ctx)
        with pytest.raises(ResourceInUseError):
            await execute_query_job("p", "d", "DROP SCHEMA `p.ds`", None, ctx)
        assert ctx.catalog.get_dataset("p", "ds") is not None
        assert ctx.catalog.get_table("p", "ds", "t") is not None

    async def test_end_to_end_cascade_drops_dataset_and_contents(
        self,
        ctx: AppContext,
    ) -> None:
        await execute_query_job("p", "c", "CREATE TABLE `p.ds.t` (id INT64)", None, ctx)
        await execute_query_job("p", "d", "DROP SCHEMA `p.ds` CASCADE", None, ctx)
        assert ctx.catalog.get_dataset("p", "ds") is None
