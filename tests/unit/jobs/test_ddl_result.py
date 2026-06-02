"""Unit tests for the single-statement DDL job result contract.

Covers :mod:`bqemulator.jobs.ddl_result` (the declared-column-list /
catalog schema sources and the existence-aware
``ddlOperationPerformed`` resolution) plus the executor's
``_finalize_statement_result`` integration via ``execute_query_job``.
The expected shapes mirror the recorded ``rest_crud/ddl_result_*``
conformance corpus: a CREATE TABLE/VIEW job result carries the
statement's analyzed schema with zero rows; ALTER / DROP / CREATE
SCHEMA return a fully empty result; TRUNCATE TABLE behaves like DML
(``numDmlAffectedRows``, no ``ddlOperationPerformed``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.jobs.ddl_result import (
    ddl_operation_for,
    ddl_result_schema_fields,
    resolve_ddl_operation,
)
from bqemulator.jobs.executor import JOB_RESULTS, JOB_SCHEMAS, execute_query_job
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


async def _run(ctx: AppContext, job_id: str, sql: str) -> dict[str, Any]:
    """Run ``sql`` through the executor; return the query statistics block."""
    meta = await execute_query_job("p", job_id, sql, None, ctx)
    stats = meta.statistics.get("query", {})
    assert isinstance(stats, dict)
    return stats


class TestDdlOperationFor:
    """Static statement-type → ``ddlOperationPerformed`` mapping."""

    @pytest.mark.parametrize(
        ("statement_type", "expected"),
        [
            ("CREATE_TABLE", "CREATE"),
            ("CREATE_VIEW", "CREATE"),
            ("DROP_TABLE", "DROP"),
            ("ALTER_TABLE", "ALTER"),
            ("CREATE_SCHEMA", "CREATE"),
            ("SELECT", ""),
            ("SCRIPT", ""),
        ],
    )
    def test_mapping(self, statement_type: str, expected: str) -> None:
        """Known DDL types map to their operation; non-DDL maps to ``""``."""
        assert ddl_operation_for(statement_type) == expected

    def test_truncate_has_no_ddl_operation(self) -> None:
        """BigQuery reports TRUNCATE like DML — no ``ddlOperationPerformed``."""
        assert ddl_operation_for("TRUNCATE_TABLE") == ""


class TestResolveDdlOperation:
    """Existence-aware CREATE / REPLACE / SKIP / DROP resolution."""

    async def test_create_table_fresh(self, ctx: AppContext) -> None:
        """A fresh target resolves to CREATE."""
        op = resolve_ddl_operation(
            "CREATE TABLE `p.ds.fresh_t` (id INT64)",
            "CREATE_TABLE",
            "p",
            ctx,
        )
        assert op == "CREATE"

    async def test_or_replace_fresh_is_create(self, ctx: AppContext) -> None:
        """``OR REPLACE`` on a fresh target still reports CREATE."""
        op = resolve_ddl_operation(
            "CREATE OR REPLACE TABLE `p.ds.fresh_t` (id INT64)",
            "CREATE_TABLE",
            "p",
            ctx,
        )
        assert op == "CREATE"

    async def test_or_replace_existing_is_replace(self, ctx: AppContext) -> None:
        """``OR REPLACE`` on an existing target reports REPLACE."""
        await _run(ctx, "rdo-1", "CREATE TABLE `p.ds.t_rep` (id INT64)")
        op = resolve_ddl_operation(
            "CREATE OR REPLACE TABLE `p.ds.t_rep` (id INT64, name STRING)",
            "CREATE_TABLE",
            "p",
            ctx,
        )
        assert op == "REPLACE"

    async def test_if_not_exists_existing_is_skip(self, ctx: AppContext) -> None:
        """``IF NOT EXISTS`` on an existing target reports SKIP."""
        await _run(ctx, "rdo-2", "CREATE TABLE `p.ds.t_skip` (id INT64)")
        op = resolve_ddl_operation(
            "CREATE TABLE IF NOT EXISTS `p.ds.t_skip` (id INT64, name STRING)",
            "CREATE_TABLE",
            "p",
            ctx,
        )
        assert op == "SKIP"

    async def test_drop_existing_is_drop(self, ctx: AppContext) -> None:
        """DROP on an existing target reports DROP."""
        await _run(ctx, "rdo-3", "CREATE TABLE `p.ds.t_drop` (id INT64)")
        op = resolve_ddl_operation("DROP TABLE `p.ds.t_drop`", "DROP_TABLE", "p", ctx)
        assert op == "DROP"

    async def test_drop_if_exists_missing_is_skip(self, ctx: AppContext) -> None:
        """``DROP … IF EXISTS`` on a missing target reports SKIP."""
        op = resolve_ddl_operation(
            "DROP TABLE IF EXISTS `p.ds.never_made`",
            "DROP_TABLE",
            "p",
            ctx,
        )
        assert op == "SKIP"

    async def test_create_schema_fresh(self, ctx: AppContext) -> None:
        """CREATE SCHEMA on a fresh dataset reports CREATE."""
        op = resolve_ddl_operation("CREATE SCHEMA `p.ds_new`", "CREATE_SCHEMA", "p", ctx)
        assert op == "CREATE"

    async def test_create_schema_if_not_exists_existing_is_skip(self, ctx: AppContext) -> None:
        """``CREATE SCHEMA IF NOT EXISTS`` on an existing dataset reports SKIP."""
        op = resolve_ddl_operation(
            "CREATE SCHEMA IF NOT EXISTS `p.ds`",
            "CREATE_SCHEMA",
            "p",
            ctx,
        )
        assert op == "SKIP"

    async def test_drop_schema_existing_is_drop(self, ctx: AppContext) -> None:
        """DROP SCHEMA on an existing dataset reports DROP."""
        op = resolve_ddl_operation("DROP SCHEMA `p.ds`", "DROP_SCHEMA", "p", ctx)
        assert op == "DROP"

    async def test_alter_table_uses_static_mapping(self, ctx: AppContext) -> None:
        """ALTER TABLE resolves through the static mapping."""
        op = resolve_ddl_operation(
            "ALTER TABLE `p.ds.t` ADD COLUMN c STRING",
            "ALTER_TABLE",
            "p",
            ctx,
        )
        assert op == "ALTER"

    async def test_unparseable_falls_back_to_static(self, ctx: AppContext) -> None:
        """Unparseable SQL falls back to the static per-type mapping."""
        op = resolve_ddl_operation("CREATE TABLE ((((", "CREATE_TABLE", "p", ctx)
        assert op == "CREATE"

    async def test_non_ddl_returns_empty(self, ctx: AppContext) -> None:
        """Non-DDL statement types resolve to ``""`` (no field written)."""
        assert resolve_ddl_operation("SELECT 1", "SELECT", "p", ctx) == ""


class TestDdlResultSchemaFields:
    """Result-schema source selection: declared column list vs catalog."""

    async def test_declared_columns_win(self, ctx: AppContext) -> None:
        """An explicit column list maps directly to REST fields."""
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.t` (id INT64, name STRING)",
            "p",
            ctx,
        )
        assert fields == [
            {"name": "id", "type": "INTEGER", "mode": "NULLABLE"},
            {"name": "name", "type": "STRING", "mode": "NULLABLE"},
        ]

    async def test_not_null_maps_to_required(self, ctx: AppContext) -> None:
        """``NOT NULL`` columns surface as ``mode=REQUIRED``."""
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.t` (id INT64 NOT NULL, name STRING)",
            "p",
            ctx,
        )
        assert fields[0] == {"name": "id", "type": "INTEGER", "mode": "REQUIRED"}
        assert fields[1]["mode"] == "NULLABLE"

    async def test_array_and_struct_columns(self, ctx: AppContext) -> None:
        """ARRAY → REPEATED element type; STRUCT → RECORD with nested fields."""
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.t` (tags ARRAY<STRING>, info STRUCT<a INT64, b STRING>)",
            "p",
            ctx,
        )
        assert fields == [
            {"name": "tags", "type": "STRING", "mode": "REPEATED"},
            {
                "name": "info",
                "type": "RECORD",
                "mode": "NULLABLE",
                "fields": [
                    {"name": "a", "type": "INTEGER", "mode": "NULLABLE"},
                    {"name": "b", "type": "STRING", "mode": "NULLABLE"},
                ],
            },
        ]

    async def test_array_of_struct(self, ctx: AppContext) -> None:
        """ARRAY<STRUCT<…>> renders as a REPEATED RECORD."""
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.t` (m ARRAY<STRUCT<x INT64>>)",
            "p",
            ctx,
        )
        assert fields == [
            {
                "name": "m",
                "type": "RECORD",
                "mode": "REPEATED",
                "fields": [{"name": "x", "type": "INTEGER", "mode": "NULLABLE"}],
            },
        ]

    async def test_ctas_uses_catalog_schema(self, ctx: AppContext) -> None:
        """CTAS (no column list) falls back to the synced catalog entry."""
        await _run(ctx, "drsf-1", "CREATE TABLE `p.ds.t_ctas` AS SELECT 1 AS id, 'x' AS nm")
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.t_ctas` AS SELECT 1 AS id, 'x' AS nm",
            "p",
            ctx,
        )
        assert [(f["name"], f["type"]) for f in fields] == [("id", "INTEGER"), ("nm", "STRING")]

    async def test_missing_catalog_entry_returns_empty(self, ctx: AppContext) -> None:
        """No column list and no catalog entry → empty schema."""
        fields = ddl_result_schema_fields(
            "CREATE TABLE `p.ds.never_synced` AS SELECT 1 AS id",
            "p",
            ctx,
        )
        assert fields == []

    async def test_non_create_returns_empty(self, ctx: AppContext) -> None:
        """A non-CREATE statement yields an empty schema."""
        assert ddl_result_schema_fields("DROP TABLE `p.ds.t`", "p", ctx) == []


class TestExecuteQueryJobDdlResults:
    """End-to-end executor behaviour for single-statement DDL jobs.

    Mirrors the recorded ``rest_crud/ddl_result_*`` conformance
    fixtures: the response schema, stored rows, and the
    ``statementType`` / ``ddlOperationPerformed`` /
    ``numDmlAffectedRows`` statistics keys.
    """

    async def test_create_table_returns_declared_schema(self, ctx: AppContext) -> None:
        """CREATE TABLE → declared schema, zero rows, CREATE."""
        stats = await _run(ctx, "ddl-1", "CREATE TABLE `p.ds.t1` (id INT64, name STRING)")
        assert JOB_SCHEMAS["ddl-1"] == [
            {"name": "id", "type": "INTEGER", "mode": "NULLABLE"},
            {"name": "name", "type": "STRING", "mode": "NULLABLE"},
        ]
        assert JOB_RESULTS["ddl-1"].num_rows == 0
        assert stats["statementType"] == "CREATE_TABLE"
        assert stats["ddlOperationPerformed"] == "CREATE"
        assert "numDmlAffectedRows" not in stats

    async def test_ctas_returns_select_schema_and_no_rows(self, ctx: AppContext) -> None:
        """CTAS → the SELECT's schema with zero rows (no leaked status row)."""
        stats = await _run(
            ctx,
            "ddl-2",
            "CREATE TABLE `p.ds.t2` AS SELECT 1 AS id, 'x' AS nm",
        )
        assert [f["name"] for f in JOB_SCHEMAS["ddl-2"]] == ["id", "nm"]
        assert JOB_RESULTS["ddl-2"].num_rows == 0
        assert stats["statementType"] == "CREATE_TABLE_AS_SELECT"
        assert stats["ddlOperationPerformed"] == "CREATE"

    async def test_if_not_exists_skip_reports_statement_schema(self, ctx: AppContext) -> None:
        """The SKIP case returns the *statement's* columns, not the table's."""
        await _run(ctx, "ddl-3a", "CREATE TABLE `p.ds.t3` (id INT64)")
        stats = await _run(
            ctx,
            "ddl-3b",
            "CREATE TABLE IF NOT EXISTS `p.ds.t3` (id INT64, name STRING)",
        )
        assert [f["name"] for f in JOB_SCHEMAS["ddl-3b"]] == ["id", "name"]
        assert stats["ddlOperationPerformed"] == "SKIP"

    async def test_or_replace_existing_reports_replace(self, ctx: AppContext) -> None:
        """``OR REPLACE`` over an existing table reports REPLACE."""
        await _run(ctx, "ddl-4a", "CREATE TABLE `p.ds.t4` (id INT64)")
        stats = await _run(
            ctx,
            "ddl-4b",
            "CREATE OR REPLACE TABLE `p.ds.t4` (id INT64, name STRING)",
        )
        assert stats["ddlOperationPerformed"] == "REPLACE"
        assert [f["name"] for f in JOB_SCHEMAS["ddl-4b"]] == ["id", "name"]

    async def test_create_view_returns_view_schema(self, ctx: AppContext) -> None:
        """CREATE VIEW → the view's output schema with zero rows."""
        await _run(ctx, "ddl-5a", "CREATE TABLE `p.ds.t5` (id INT64, name STRING)")
        stats = await _run(
            ctx,
            "ddl-5b",
            "CREATE VIEW `p.ds.v5` AS SELECT id, name FROM `p.ds.t5`",
        )
        assert [f["name"] for f in JOB_SCHEMAS["ddl-5b"]] == ["id", "name"]
        assert JOB_RESULTS["ddl-5b"].num_rows == 0
        assert stats["statementType"] == "CREATE_VIEW"
        assert stats["ddlOperationPerformed"] == "CREATE"

    async def test_alter_table_returns_empty_result(self, ctx: AppContext) -> None:
        """ALTER TABLE → empty schema and rows, operation ALTER."""
        await _run(ctx, "ddl-6a", "CREATE TABLE `p.ds.t6` (id INT64)")
        stats = await _run(ctx, "ddl-6b", "ALTER TABLE `p.ds.t6` ADD COLUMN name STRING")
        assert JOB_SCHEMAS["ddl-6b"] == []
        assert JOB_RESULTS["ddl-6b"].num_rows == 0
        assert stats["ddlOperationPerformed"] == "ALTER"

    async def test_drop_table_returns_empty_result(self, ctx: AppContext) -> None:
        """DROP TABLE → empty schema and rows, operation DROP."""
        await _run(ctx, "ddl-7a", "CREATE TABLE `p.ds.t7` (id INT64)")
        stats = await _run(ctx, "ddl-7b", "DROP TABLE `p.ds.t7`")
        assert JOB_SCHEMAS["ddl-7b"] == []
        assert stats["statementType"] == "DROP_TABLE"
        assert stats["ddlOperationPerformed"] == "DROP"

    async def test_drop_if_exists_missing_reports_skip(self, ctx: AppContext) -> None:
        """``DROP TABLE IF EXISTS`` on a missing table reports SKIP."""
        stats = await _run(ctx, "ddl-8", "DROP TABLE IF EXISTS `p.ds.no_such`")
        assert JOB_SCHEMAS["ddl-8"] == []
        assert stats["ddlOperationPerformed"] == "SKIP"

    async def test_truncate_behaves_like_dml(self, ctx: AppContext) -> None:
        """TRUNCATE → empty result, ``numDmlAffectedRows``, no DDL operation."""
        await _run(ctx, "ddl-9a", "CREATE TABLE `p.ds.t9` (id INT64)")
        await _run(ctx, "ddl-9b", "INSERT INTO `p.ds.t9` (id) VALUES (1), (2)")
        stats = await _run(ctx, "ddl-9c", "TRUNCATE TABLE `p.ds.t9`")
        assert JOB_SCHEMAS["ddl-9c"] == []
        assert JOB_RESULTS["ddl-9c"].num_rows == 0
        assert stats["statementType"] == "TRUNCATE_TABLE"
        assert stats["numDmlAffectedRows"] == "2"
        assert "ddlOperationPerformed" not in stats

    async def test_create_and_drop_schema_return_empty(self, ctx: AppContext) -> None:
        """CREATE SCHEMA / DROP SCHEMA → empty result with CREATE / DROP."""
        create_stats = await _run(ctx, "ddl-10a", "CREATE SCHEMA `p.ds10`")
        assert JOB_SCHEMAS["ddl-10a"] == []
        assert create_stats["statementType"] == "CREATE_SCHEMA"
        assert create_stats["ddlOperationPerformed"] == "CREATE"
        drop_stats = await _run(ctx, "ddl-10b", "DROP SCHEMA `p.ds10`")
        assert JOB_SCHEMAS["ddl-10b"] == []
        assert drop_stats["statementType"] == "DROP_SCHEMA"
        assert drop_stats["ddlOperationPerformed"] == "DROP"

    async def test_select_passes_through_unchanged(self, ctx: AppContext) -> None:
        """A SELECT keeps its rows and schema (no DDL shaping applied)."""
        stats = await _run(ctx, "ddl-11", "SELECT 1 AS one")
        assert JOB_SCHEMAS["ddl-11"] == [{"name": "one", "type": "INTEGER", "mode": "NULLABLE"}]
        assert JOB_RESULTS["ddl-11"].num_rows == 1
        assert stats["statementType"] == "SELECT"
        assert "ddlOperationPerformed" not in stats
