"""Unit tests for UDF runtime materialization + dispatch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.catalog.models import RoutineArgument, RoutineMeta
from bqemulator.config import Settings
from bqemulator.domain.errors import InvalidQueryError
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.udf.sql_udf import SQLUDFRuntime
from bqemulator.udf.table_valued import TableValuedRuntime

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _routine(
    rid: str,
    *,
    rtype: str = "SCALAR_FUNCTION",
    language: str = "SQL",
    body: str = "x + 1",
    arguments: tuple[RoutineArgument, ...] = (
        RoutineArgument(name="x", data_type={"typeKind": "INT64"}),
    ),
    return_type: dict[str, object] | None = None,
) -> RoutineMeta:
    return RoutineMeta(
        project_id="p",
        dataset_id="ds",
        routine_id=rid,
        routine_type=rtype,
        language=language,
        definition_body=body,
        arguments=arguments,
        return_type=return_type or {"typeKind": "INT64"},
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


@pytest_asyncio.fixture
async def engine(ephemeral_settings: Settings) -> AsyncIterator[DuckDBEngine]:
    e = DuckDBEngine(ephemeral_settings)
    await e.start()
    try:
        yield e
    finally:
        await e.stop()


class TestSQLUDFRuntime:
    def test_materialize_and_call(self, engine: DuckDBEngine) -> None:
        rt = SQLUDFRuntime()
        r = _routine("add_one")
        rt.materialize(r, engine)
        (result,) = engine.execute("SELECT p__ds__add_one(5)").fetchone()
        assert result == 6

    def test_or_replace_idempotent(self, engine: DuckDBEngine) -> None:
        rt = SQLUDFRuntime()
        rt.materialize(_routine("f", body="x + 1"), engine)
        rt.materialize(_routine("f", body="x + 2"), engine)  # replace
        (result,) = engine.execute("SELECT p__ds__f(10)").fetchone()
        assert result == 12

    def test_deregister_removes_macro(self, engine: DuckDBEngine) -> None:
        rt = SQLUDFRuntime()
        r = _routine("to_drop")
        rt.materialize(r, engine)
        rt.deregister(r, engine)
        with pytest.raises(Exception):  # duckdb catalog error
            engine.execute("SELECT p__ds__to_drop(1)").fetchone()

    def test_invalid_body_raises(self, engine: DuckDBEngine) -> None:
        rt = SQLUDFRuntime()
        with pytest.raises(InvalidQueryError):
            rt.materialize(_routine("bad", body="NOT_A_FUNCTION(("), engine)


class TestTableValuedRuntime:
    def test_materialize_and_call(self, engine: DuckDBEngine) -> None:
        rt = TableValuedRuntime()
        r = _routine(
            "gen",
            rtype="TABLE_VALUED_FUNCTION",
            body="SELECT v FROM UNNEST(GENERATE_ARRAY(1, n)) AS v",
            arguments=(RoutineArgument(name="n", data_type={"typeKind": "INT64"}),),
            return_type=None,
        )
        rt.materialize(r, engine)
        rows = engine.execute("SELECT * FROM p__ds__gen(3)").fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]

    def test_deregister(self, engine: DuckDBEngine) -> None:
        rt = TableValuedRuntime()
        r = _routine(
            "gen2",
            rtype="TABLE_VALUED_FUNCTION",
            body="SELECT 1 AS v",
            arguments=(),
            return_type=None,
        )
        rt.materialize(r, engine)
        rt.deregister(r, engine)

    def test_invalid_tvf_body_raises(self, engine: DuckDBEngine) -> None:
        rt = TableValuedRuntime()
        with pytest.raises(InvalidQueryError):
            rt.materialize(
                _routine(
                    "badtvf",
                    rtype="TABLE_VALUED_FUNCTION",
                    body="SELECT FROM )",
                    arguments=(),
                    return_type=None,
                ),
                engine,
            )


class TestUDFRegistry:
    def test_dispatch_sql(self, engine: DuckDBEngine, ephemeral_settings: Settings) -> None:
        reg = UDFRegistry(ephemeral_settings)
        r = _routine("rsql")
        reg.materialize(r, engine)
        (result,) = engine.execute("SELECT p__ds__rsql(10)").fetchone()
        assert result == 11

    def test_dispatch_procedure_is_noop(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        reg = UDFRegistry(ephemeral_settings)
        r = _routine(
            "rproc",
            rtype="PROCEDURE",
            arguments=(),
            body="SELECT 1;",
            return_type=None,
        )
        # Should not raise and not create anything in DuckDB.
        reg.materialize(r, engine)
        reg.deregister(r, engine)

    def test_dispatch_tvf(self, engine: DuckDBEngine, ephemeral_settings: Settings) -> None:
        reg = UDFRegistry(ephemeral_settings)
        r = _routine(
            "rtvf",
            rtype="TABLE_VALUED_FUNCTION",
            body="SELECT 1 AS v",
            arguments=(),
            return_type=None,
        )
        reg.materialize(r, engine)
        rows = engine.execute("SELECT * FROM p__ds__rtvf()").fetchall()
        assert rows == [(1,)]

    def test_unknown_combination_raises(
        self,
        engine: DuckDBEngine,  # noqa: ARG002
        ephemeral_settings: Settings,
    ) -> None:
        reg = UDFRegistry(ephemeral_settings)
        # Build a RoutineMeta then monkey-patch the language to an
        # unsupported value — the pydantic literal keeps malformed
        # values out of the catalog but the dispatcher still handles
        # them defensively.
        r = _routine("x", rtype="SCALAR_FUNCTION", language="SQL")
        object.__setattr__(r, "language", "PYTHON")
        with pytest.raises(InvalidQueryError, match="Unsupported"):
            reg._dispatch(r)


class TestJSUDFRuntime:
    def test_registers_and_calls(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        from bqemulator.udf.js_udf import JavaScriptUDFRuntime

        rt = JavaScriptUDFRuntime(
            cpu_timeout_ms=ephemeral_settings.udf_js_timeout_ms,
            memory_limit_bytes=ephemeral_settings.udf_js_memory_bytes,
        )
        r = _routine(
            "jsd",
            language="JAVASCRIPT",
            body="return x * 2;",
            return_type={"typeKind": "INT64"},
        )
        rt.materialize(r, engine)
        (result,) = engine.execute("SELECT p__ds__jsd(21)").fetchone()
        assert result == 42

    def test_bad_js_raises_on_materialize(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        from bqemulator.udf.js_udf import JavaScriptUDFRuntime

        rt = JavaScriptUDFRuntime(
            cpu_timeout_ms=ephemeral_settings.udf_js_timeout_ms,
            memory_limit_bytes=ephemeral_settings.udf_js_memory_bytes,
        )
        r = _routine(
            "jsbad",
            language="JAVASCRIPT",
            body="this is not valid javascript ((((",
            return_type={"typeKind": "INT64"},
        )
        with pytest.raises(InvalidQueryError):
            rt.materialize(r, engine)

    def test_deregister(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        from bqemulator.udf.js_udf import JavaScriptUDFRuntime

        rt = JavaScriptUDFRuntime(
            cpu_timeout_ms=ephemeral_settings.udf_js_timeout_ms,
            memory_limit_bytes=ephemeral_settings.udf_js_memory_bytes,
        )
        r = _routine(
            "jsdrop",
            language="JAVASCRIPT",
            body="return x + 1;",
            return_type={"typeKind": "INT64"},
        )
        rt.materialize(r, engine)
        rt.deregister(r, engine)
        # Second deregister is idempotent.
        rt.deregister(r, engine)
