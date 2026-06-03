"""Unit tests for single routine-DDL ``statementType`` + execution.

Covers :mod:`bqemulator.jobs.routine_ddl` and its executor integration:
a single ``CREATE FUNCTION`` / ``CREATE TABLE FUNCTION`` reports
``CREATE_FUNCTION`` / ``CREATE_TABLE_FUNCTION`` (not ``SCRIPT``);
``CREATE PROCEDURE`` deliberately stays ``SCRIPT`` (matching real
BigQuery); and ``DROP FUNCTION`` / ``DROP PROCEDURE`` /
``DROP TABLE FUNCTION`` execute against the catalog + UDF registry
(rather than DuckDB, which rejects them) and report the matching
``DROP_*`` type. Expected shapes mirror the recorded
``routines_scripting/routine_ddl_*`` conformance corpus.
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
from bqemulator.domain.errors import NotFoundError
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import execute_query_job
from bqemulator.jobs.routine_ddl import (
    classify_create_routine,
    detect_drop_routine,
    resolve_create_routine_operation,
    run_drop_routine,
)
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.scripting.parser import parse_script
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
            catalog=catalog,
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


_SCALAR = "CREATE FUNCTION `p.ds.f`(x INT64) RETURNS INT64 AS (x + 1)"
_JS = "CREATE FUNCTION `p.ds.jsf`(x FLOAT64) RETURNS FLOAT64 LANGUAGE js AS 'return x*2;'"
_TVF = "CREATE TABLE FUNCTION `p.ds.tvf`(n INT64) AS SELECT n AS x"
_PROC = "CREATE PROCEDURE `p.ds.pr`(x INT64) BEGIN SELECT 1 AS one; END"


class TestClassifyCreateRoutine:
    """``classify_create_routine`` maps the scripting AST to a statementType."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            (_SCALAR, "CREATE_FUNCTION"),
            (_JS, "CREATE_FUNCTION"),
            (_TVF, "CREATE_TABLE_FUNCTION"),
            (_PROC, ""),  # procedure stays SCRIPT — empty means "no override"
        ],
    )
    def test_classification(self, sql: str, expected: str) -> None:
        assert classify_create_routine(parse_script(sql)) == expected

    def test_multi_statement_script_not_classified(self) -> None:
        """A genuine multi-statement script is not a single CREATE routine."""
        script = parse_script(f"{_SCALAR}; SELECT 1 AS a")
        assert classify_create_routine(script) == ""

    def test_non_routine_single_statement(self) -> None:
        """A single non-routine statement returns ``""``."""
        assert classify_create_routine(parse_script("SELECT 1 AS a")) == ""


class TestResolveCreateRoutineOperation:
    """CREATE vs REPLACE for a single CREATE FUNCTION / TVF."""

    async def test_fresh_is_create(self, ctx: AppContext) -> None:
        assert resolve_create_routine_operation(parse_script(_SCALAR), "p", ctx) == "CREATE"

    async def test_or_replace_fresh_is_create(self, ctx: AppContext) -> None:
        script = parse_script("CREATE OR REPLACE FUNCTION `p.ds.f`(x INT64) RETURNS INT64 AS (x)")
        assert resolve_create_routine_operation(script, "p", ctx) == "CREATE"

    async def test_or_replace_existing_is_replace(self, ctx: AppContext) -> None:
        await _run(ctx, "c1", _SCALAR)
        script = parse_script(
            "CREATE OR REPLACE FUNCTION `p.ds.f`(x INT64) RETURNS INT64 AS (x + 9)",
        )
        assert resolve_create_routine_operation(script, "p", ctx) == "REPLACE"

    async def test_procedure_returns_empty(self, ctx: AppContext) -> None:
        """A procedure has no ``ddlOperationPerformed`` (it reports SCRIPT)."""
        assert resolve_create_routine_operation(parse_script(_PROC), "p", ctx) == ""


class TestDetectDropRoutine:
    """``detect_drop_routine`` recognises the three routine-drop forms."""

    def test_drop_function(self) -> None:
        ref = detect_drop_routine("DROP FUNCTION `p.ds.f`", "p")
        assert ref is not None
        assert ref.statement_type == "DROP_FUNCTION"
        assert (ref.project_id, ref.dataset_id, ref.routine_id) == ("p", "ds", "f")
        assert ref.if_exists is False

    def test_drop_function_if_exists(self) -> None:
        ref = detect_drop_routine("DROP FUNCTION IF EXISTS `p.ds.f`", "p")
        assert ref is not None
        assert ref.if_exists is True

    def test_drop_procedure(self) -> None:
        ref = detect_drop_routine("DROP PROCEDURE `p.ds.pr`", "p")
        assert ref is not None
        assert ref.statement_type == "DROP_PROCEDURE"

    def test_drop_table_function(self) -> None:
        """DROP TABLE FUNCTION is matched by regex (sqlglot can't parse it)."""
        ref = detect_drop_routine("DROP TABLE FUNCTION `p.ds.tvf`", "p")
        assert ref is not None
        assert ref.statement_type == "DROP_TABLE_FUNCTION"
        assert (ref.project_id, ref.dataset_id, ref.routine_id) == ("p", "ds", "tvf")

    def test_drop_table_function_if_exists(self) -> None:
        ref = detect_drop_routine("DROP TABLE FUNCTION IF EXISTS `p.ds.tvf`", "p")
        assert ref is not None
        assert ref.statement_type == "DROP_TABLE_FUNCTION"
        assert ref.if_exists is True

    def test_dataset_qualified_resolves_default_project(self) -> None:
        ref = detect_drop_routine("DROP FUNCTION `ds.f`", "p")
        assert ref is not None
        assert (ref.project_id, ref.dataset_id, ref.routine_id) == ("p", "ds", "f")

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE `p.ds.t`",
            "DROP VIEW `p.ds.v`",
            "DROP SCHEMA `p.ds`",
            "SELECT 1",
            "CREATE FUNCTION `p.ds.f`(x INT64) AS (x)",
        ],
    )
    def test_non_routine_drop_returns_none(self, sql: str) -> None:
        assert detect_drop_routine(sql, "p") is None


class TestRunDropRoutine:
    """``run_drop_routine`` removes the routine and reports DROP / SKIP."""

    async def test_drop_existing(self, ctx: AppContext) -> None:
        await _run(ctx, "c1", _SCALAR)
        assert ctx.catalog.get_routine("p", "ds", "f") is not None
        ref = detect_drop_routine("DROP FUNCTION `p.ds.f`", "p")
        assert ref is not None
        assert await run_drop_routine(ref, ctx) == "DROP"
        assert ctx.catalog.get_routine("p", "ds", "f") is None

    async def test_drop_if_exists_missing_skips(self, ctx: AppContext) -> None:
        ref = detect_drop_routine("DROP FUNCTION IF EXISTS `p.ds.gone`", "p")
        assert ref is not None
        assert await run_drop_routine(ref, ctx) == "SKIP"

    async def test_drop_missing_without_if_exists_raises(self, ctx: AppContext) -> None:
        ref = detect_drop_routine("DROP FUNCTION `p.ds.gone`", "p")
        assert ref is not None
        with pytest.raises(NotFoundError):
            await run_drop_routine(ref, ctx)


class TestExecuteQueryJobRoutineDDL:
    """End-to-end statementType + side effects through ``execute_query_job``."""

    async def test_create_scalar_function(self, ctx: AppContext) -> None:
        stats = await _run(ctx, "j1", _SCALAR)
        assert stats["statementType"] == "CREATE_FUNCTION"
        assert stats["ddlOperationPerformed"] == "CREATE"
        assert ctx.catalog.get_routine("p", "ds", "f") is not None

    async def test_create_js_function(self, ctx: AppContext) -> None:
        stats = await _run(ctx, "j2", _JS)
        assert stats["statementType"] == "CREATE_FUNCTION"
        assert stats["ddlOperationPerformed"] == "CREATE"

    async def test_create_table_function(self, ctx: AppContext) -> None:
        stats = await _run(ctx, "j3", _TVF)
        assert stats["statementType"] == "CREATE_TABLE_FUNCTION"
        assert stats["ddlOperationPerformed"] == "CREATE"

    async def test_create_procedure_stays_script(self, ctx: AppContext) -> None:
        """CREATE PROCEDURE reports SCRIPT with no ddlOperationPerformed (real BQ)."""
        stats = await _run(ctx, "j4", _PROC)
        assert stats["statementType"] == "SCRIPT"
        assert "ddlOperationPerformed" not in stats
        assert ctx.catalog.get_routine("p", "ds", "pr") is not None

    async def test_create_or_replace_existing_reports_replace(self, ctx: AppContext) -> None:
        await _run(ctx, "j5a", _SCALAR)
        stats = await _run(
            ctx,
            "j5b",
            "CREATE OR REPLACE FUNCTION `p.ds.f`(x INT64) RETURNS INT64 AS (x + 5)",
        )
        assert stats["statementType"] == "CREATE_FUNCTION"
        assert stats["ddlOperationPerformed"] == "REPLACE"

    async def test_drop_function_executes_and_classifies(self, ctx: AppContext) -> None:
        await _run(ctx, "j6a", _SCALAR)
        stats = await _run(ctx, "j6b", "DROP FUNCTION `p.ds.f`")
        assert stats["statementType"] == "DROP_FUNCTION"
        assert stats["ddlOperationPerformed"] == "DROP"
        assert ctx.catalog.get_routine("p", "ds", "f") is None

    async def test_drop_procedure_executes(self, ctx: AppContext) -> None:
        """DROP PROCEDURE no longer raises (DuckDB has no such statement)."""
        await _run(ctx, "j7a", "CREATE PROCEDURE `p.ds.pr`() BEGIN END")
        stats = await _run(ctx, "j7b", "DROP PROCEDURE `p.ds.pr`")
        assert stats["statementType"] == "DROP_PROCEDURE"
        assert stats["ddlOperationPerformed"] == "DROP"
        assert ctx.catalog.get_routine("p", "ds", "pr") is None

    async def test_drop_table_function_executes(self, ctx: AppContext) -> None:
        await _run(ctx, "j8a", _TVF)
        stats = await _run(ctx, "j8b", "DROP TABLE FUNCTION `p.ds.tvf`")
        assert stats["statementType"] == "DROP_TABLE_FUNCTION"
        assert stats["ddlOperationPerformed"] == "DROP"
        assert ctx.catalog.get_routine("p", "ds", "tvf") is None

    async def test_drop_function_if_exists_missing_reports_skip(self, ctx: AppContext) -> None:
        stats = await _run(ctx, "j9", "DROP FUNCTION IF EXISTS `p.ds.never`")
        assert stats["statementType"] == "DROP_FUNCTION"
        assert stats["ddlOperationPerformed"] == "SKIP"

    async def test_create_then_call_then_drop_roundtrip(self, ctx: AppContext) -> None:
        """A created scalar UDF is usable, then drops cleanly (registry + macro)."""
        await _run(ctx, "j10a", _SCALAR)
        used = await _run(ctx, "j10b", "SELECT `p.ds.f`(41) AS r")
        assert used["statementType"] == "SELECT"
        await _run(ctx, "j10c", "DROP FUNCTION `p.ds.f`")
        assert ctx.catalog.get_routine("p", "ds", "f") is None
