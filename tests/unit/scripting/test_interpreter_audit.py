"""Production-audit coverage for the scripting interpreter.

Focused tests on the error paths and resource caps that the happy-path
suite doesn't exercise: SQL injection through script variables,
unmatched exceptions, RAISE inside a handler, procedure-arg mismatches,
EXECUTE IMMEDIATE error surfaces, max-statements quota, etc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, RoutineArgument, RoutineMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import (
    InvalidQueryError,
    QuotaExceededError,
    ValidationError,
)
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.scripting.interpreter import run_script
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


@pytest_asyncio.fixture
async def ctx(ephemeral_settings: Settings) -> AsyncIterator[AppContext]:
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


@pytest_asyncio.fixture
async def ctx_tight_caps(ephemeral_settings: Settings) -> AsyncIterator[AppContext]:
    """AppContext with aggressively low script caps to verify quota enforcement."""
    settings = ephemeral_settings.model_copy(
        update={
            "scripting_max_statements": 10,
            "scripting_max_loop_iterations": 4,
        },
    )
    engine = DuckDBEngine(settings)
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
    context = AppContext(
        settings=settings,
        clock=FrozenClock(NOW),
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=EventBus(),
        udf_registry=UDFRegistry(settings),
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


class TestInjectionDefense:
    async def test_string_with_quote_as_var_value(self, ctx: AppContext) -> None:
        """A SET assigning a value with ' does not escape into DuckDB."""
        script = """
DECLARE s STRING DEFAULT "benign' OR 1=1--";
SELECT s;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        # The value round-trips unchanged as a bound parameter.
        assert result.final_table.column(0).to_pylist() == ["benign' OR 1=1--"]

    async def test_script_variable_with_semicolon_safe(self, ctx: AppContext) -> None:
        """Values containing ; cannot break out of the host statement.

        The string ``x; DROP TABLE t; --`` is 19 characters. If the value
        were interpolated into the SQL text instead of bound, DuckDB
        would parse the ``;`` as a statement terminator and the
        ``DROP TABLE`` as a separate statement — which would either fail
        (no such table) or succeed in a catastrophic way. We want neither.
        """
        payload = "x; DROP TABLE t; --"
        script = f"""
DECLARE payload STRING DEFAULT '{payload}';
SELECT LENGTH(payload);
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == [len(payload)]


class TestQuotas:
    async def test_max_statements_triggers_quota(self, ctx_tight_caps: AppContext) -> None:
        # cap is 10 statements; this script runs 13+.
        script = """
DECLARE a INT64 DEFAULT 0;
DECLARE b INT64 DEFAULT 0;
DECLARE c INT64 DEFAULT 0;
DECLARE d INT64 DEFAULT 0;
DECLARE e INT64 DEFAULT 0;
SET a = 1;
SET b = 2;
SET c = 3;
SET d = 4;
SET e = 5;
SELECT a;
SELECT b;
SELECT c;
"""
        with pytest.raises(QuotaExceededError, match="statement count"):
            await run_script(ctx_tight_caps, "p", script)

    async def test_max_loop_iterations_triggers_quota(
        self,
        ctx_tight_caps: AppContext,
    ) -> None:
        script = """
DECLARE i INT64 DEFAULT 0;
WHILE i < 100 DO SET i = i + 1; END WHILE;
"""
        with pytest.raises(QuotaExceededError, match="WHILE exceeded"):
            await run_script(ctx_tight_caps, "p", script)

    async def test_loop_iteration_quota(self, ctx_tight_caps: AppContext) -> None:
        script = """
LOOP
  SET x = 1;
END LOOP;
"""
        # Each iteration does SET which is unknown — will raise InvalidQueryError
        # first. But loop-iteration check should fire on tight caps. Use a
        # script that doesn't error out.
        script = """
DECLARE i INT64 DEFAULT 0;
LOOP
  SET i = i + 1;
END LOOP;
"""
        with pytest.raises(QuotaExceededError, match="LOOP exceeded"):
            await run_script(ctx_tight_caps, "p", script)

    async def test_for_iteration_quota(self, ctx_tight_caps: AppContext) -> None:
        # Tight cap is 4; iterate over 5 rows.
        script = """
DECLARE s INT64 DEFAULT 0;
FOR row IN (SELECT x FROM UNNEST([1,2,3,4,5]) AS x) DO
  SET s = s + row.x;
END FOR;
"""
        with pytest.raises(QuotaExceededError, match="FOR exceeded"):
            await run_script(ctx_tight_caps, "p", script)


class TestErrorPaths:
    async def test_call_unknown_procedure_404(self, ctx: AppContext) -> None:
        with pytest.raises(Exception, match="Not found"):
            await run_script(ctx, "p", "CALL ds.missing(1);")

    async def test_call_wrong_arg_count(self, ctx: AppContext) -> None:
        # Register a procedure expecting one arg, then call with two.
        now = NOW
        ctx.catalog.create_routine(
            RoutineMeta(
                project_id="p",
                dataset_id="ds",
                routine_id="p1",
                routine_type="PROCEDURE",
                language="SQL",
                definition_body="SELECT 1;",
                arguments=(RoutineArgument(name="x", data_type={"typeKind": "INT64"}),),
                creation_time=now,
                last_modified_time=now,
                etag="e",
            ),
        )
        with pytest.raises(InvalidQueryError, match="expects 1 arguments"):
            await run_script(ctx, "p", "CALL ds.p1(1, 2);")

    async def test_execute_immediate_non_string_raises(self, ctx: AppContext) -> None:
        script = """
DECLARE q INT64 DEFAULT 42;
EXECUTE IMMEDIATE q;
"""
        with pytest.raises(InvalidQueryError, match="STRING expression"):
            await run_script(ctx, "p", script)

    async def test_execute_immediate_no_rows_into(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64;
EXECUTE IMMEDIATE 'SELECT 1 WHERE FALSE' INTO x;
"""
        with pytest.raises(InvalidQueryError, match="no rows"):
            await run_script(ctx, "p", script)

    async def test_execute_immediate_column_count_mismatch(
        self,
        ctx: AppContext,
    ) -> None:
        script = """
DECLARE a INT64;
EXECUTE IMMEDIATE 'SELECT 1, 2' INTO a;
"""
        with pytest.raises(InvalidQueryError, match="target count"):
            await run_script(ctx, "p", script)

    async def test_set_multi_target_col_count_mismatch(self, ctx: AppContext) -> None:
        script = """
DECLARE a INT64 DEFAULT 0;
DECLARE b INT64 DEFAULT 0;
SET (a, b) = (SELECT 1);
"""
        with pytest.raises(InvalidQueryError, match="column count"):
            await run_script(ctx, "p", script)

    async def test_routine_create_validates_id(self, ctx: AppContext) -> None:
        # A CREATE FUNCTION with an injected id should be rejected at
        # materialization time via the SQL-id whitelist.
        script = """
CREATE FUNCTION `ds.evil; DROP TABLE t`(x INT64) AS (x + 1);
"""
        with pytest.raises((InvalidQueryError, ValidationError)):
            await run_script(ctx, "p", script)

    async def test_raise_wraps_message(self, ctx: AppContext) -> None:
        script = """
BEGIN
  RAISE USING MESSAGE = 'custom error';
EXCEPTION WHEN ERROR THEN
  SELECT __error_message__ AS m;
END;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == ["custom error"]


class TestConcurrency:
    async def test_concurrent_routine_invocations(self, ctx: AppContext) -> None:
        """Routines created via REST can be invoked from a concurrent script."""
        import asyncio

        # Register a SQL UDF.
        now = NOW
        r = RoutineMeta(
            project_id="p",
            dataset_id="ds",
            routine_id="bumper",
            routine_type="SCALAR_FUNCTION",
            language="SQL",
            definition_body="x + 100",
            arguments=(RoutineArgument(name="x", data_type={"typeKind": "INT64"}),),
            return_type={"typeKind": "INT64"},
            creation_time=now,
            last_modified_time=now,
            etag="e",
        )
        ctx.catalog.create_routine(r)
        async with ctx.engine.write_lock():
            ctx.udf_registry.materialize(r, ctx.engine)

        # Concurrent scripts invoking the routine should all succeed.
        async def one() -> int:
            result = await run_script(
                ctx,
                "p",
                "SELECT ds.bumper(5) AS v;",
            )
            return result.final_table.column(0).to_pylist()[0]

        results = await asyncio.gather(*(one() for _ in range(10)))
        assert all(v == 105 for v in results)


class TestHydration:
    async def test_registry_hydrate_restores_routines(
        self,
        ephemeral_settings: Settings,
    ) -> None:
        """After a fresh UDFRegistry hydrates from the catalog, routines work."""
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
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
            r = RoutineMeta(
                project_id="p",
                dataset_id="ds",
                routine_id="hydrated",
                routine_type="SCALAR_FUNCTION",
                language="SQL",
                definition_body="x * 10",
                arguments=(RoutineArgument(name="x", data_type={"typeKind": "INT64"}),),
                return_type={"typeKind": "INT64"},
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
            )
            catalog.create_routine(r)

            # Fresh registry; hydrate; then invoke.
            reg = UDFRegistry(ephemeral_settings)
            reg.hydrate(catalog, engine)
            (result,) = engine.execute("SELECT p__ds__hydrated(3)").fetchone()
            assert result == 30
        finally:
            await engine.stop()
