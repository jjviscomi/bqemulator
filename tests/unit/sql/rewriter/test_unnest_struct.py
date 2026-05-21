"""Tests for the ``UNNEST(array<STRUCT>)`` field-name-propagation pre-translator.

:mod:`bqemulator.sql.rewriter.unnest_struct` propagates the first
struct's named-field aliases to every subsequent positional struct
inside an ``UNNEST([...])`` array literal — preserving BigQuery's
"first struct seeds the field names" semantic so the downstream
``rewrite_struct_helpers`` pass leaves the array alone, and SQLGlot's
natural BigQuery → DuckDB transpile emits a destructurable
``(SELECT UNNEST(..., max_depth => 2))`` shape.

Closes the ``routines_scripting/script_for_iterate_into_table``
conformance fixture: the FOR-loop's source ``SELECT label, value FROM
UNNEST([STRUCT('a' AS label, 1 AS value), STRUCT('b', 2), STRUCT('c',
3)]) ORDER BY label`` failed at DuckDB's binder pre-fix because
``rewrite_struct_helpers`` had rewritten the unnamed structs to
``ROW(...)`` form, producing a mixed-shape DuckDB array.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import duckdb
import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.domain.result import Ok
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.scripting.interpreter import ScriptInterpreter
from bqemulator.sql.rewriter.unnest_struct import rewrite_unnest_struct
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


class TestMixedPositionalStructs:
    """Propagate first-struct names through positional siblings."""

    def test_recorded_fixture_shape_translates_and_executes(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """The recorded ``script_for_iterate_into_table`` shape translates cleanly.

        Before this rewriter, ``rewrite_struct_helpers`` would convert the
        unnamed ``STRUCT('b', 2)`` to ``ROW('b', 2)``, producing a mixed
        array DuckDB rejected with ``Referenced column "label" not
        found in FROM clause! Candidate bindings: "unnest"``.
        """
        sql = (
            "SELECT label, value FROM UNNEST([\n"
            "  STRUCT('a' AS label, 1 AS value),\n"
            "  STRUCT('b', 2),\n"
            "  STRUCT('c', 3)\n"
            "]) ORDER BY label"
        )
        result = t.translate(sql)
        assert isinstance(result, Ok), result
        assert con.execute(result.value).fetchall() == [
            ("a", 1),
            ("b", 2),
            ("c", 3),
        ]

    def test_rewrite_propagates_field_names_to_siblings(self) -> None:
        sql = (
            "SELECT label, value FROM UNNEST([\n"
            "  STRUCT('a' AS label, 1 AS value),\n"
            "  STRUCT('b', 2)\n"
            "])"
        )
        rewritten = rewrite_unnest_struct(sql)
        # Both struct elements should now carry ``AS label`` + ``AS value``.
        assert rewritten.count("AS label") == 2
        assert rewritten.count("AS value") == 2

    def test_order_by_struct_field_works_after_rewrite(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """ORDER BY on a struct-field column resolves through the destructure."""
        sql = (
            "SELECT label FROM UNNEST([\n"
            "  STRUCT('c' AS label, 3 AS value),\n"
            "  STRUCT('a', 1),\n"
            "  STRUCT('b', 2)\n"
            "]) ORDER BY label"
        )
        result = t.translate(sql)
        assert isinstance(result, Ok), result
        rows = con.execute(result.value).fetchall()
        assert rows == [("a",), ("b",), ("c",)]


class TestRegressionGuards:
    """Shapes the rewriter must NOT touch."""

    def test_scalar_unnest_with_aggregate_unchanged(self) -> None:
        """``UNNEST([2, 3, 5]) AS v`` — bare scalar array → no rewrite.

        Mirrors the ``script_for_iterate_with_aggregate`` conformance
        fixture: a scalar UNNEST that already binds via the user's
        explicit alias must continue to work without the
        struct-field-propagation pass touching it.
        """
        sql = "SELECT v FROM UNNEST([2, 3, 5]) AS v ORDER BY v"
        # The rewriter returns the input verbatim when no struct
        # propagation is possible (the array's elements are scalars).
        assert rewrite_unnest_struct(sql) is sql

    def test_scalar_unnest_still_executes_end_to_end(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT v FROM UNNEST([2, 3, 5]) AS v ORDER BY v")
        assert isinstance(result, Ok), result
        assert con.execute(result.value).fetchall() == [(2,), (3,), (5,)]

    def test_all_named_array_unchanged(self) -> None:
        """If every struct already carries field names, the rewriter is a no-op."""
        sql = (
            "SELECT label FROM UNNEST([\n"
            "  STRUCT('a' AS label, 1 AS value),\n"
            "  STRUCT('b' AS label, 2 AS value)\n"
            "])"
        )
        assert rewrite_unnest_struct(sql) is sql

    def test_all_positional_array_unchanged(self) -> None:
        """If the first struct is positional, no names are propagated."""
        sql = "SELECT * FROM UNNEST([STRUCT('a', 1), STRUCT('b', 2)])"
        # First struct is positional → BigQuery treats the whole array as
        # positional. The rewriter declines to fabricate names.
        assert rewrite_unnest_struct(sql) is sql

    def test_input_without_unnest_short_circuits(self) -> None:
        """No ``UNNEST`` keyword in the SQL → identity pass through."""
        sql = "SELECT 1, 2, 3"
        assert rewrite_unnest_struct(sql) is sql

    def test_unparseable_input_short_circuits(self) -> None:
        """A garbage UNNEST string must not crash — downstream surfaces the parse error."""
        sql = "UNNEST garbage :: not sql"
        # The function MUST NOT raise; it returns the input unchanged
        # so the downstream SQLGlot transpile can produce its own
        # parse error.
        out = rewrite_unnest_struct(sql)
        assert out == sql

    def test_arity_mismatch_left_alone(self) -> None:
        """A struct whose arity doesn't match the first is left untouched.

        BigQuery itself would reject the literal — we defer to the
        downstream parser to surface the diagnostic rather than masking
        it with a name-propagation guess.
        """
        sql = "SELECT * FROM UNNEST([\n  STRUCT('a' AS label, 1 AS value),\n  STRUCT('b')\n])"
        rewritten = rewrite_unnest_struct(sql)
        # The single-element second struct doesn't match the first's
        # arity, so the rewriter must NOT rewrite it.
        # (Either no change at all, or the rewriter touches nothing
        # — both acceptable; here we assert the second STRUCT survives.)
        assert "STRUCT('b')" in rewritten or 'STRUCT("b")' in rewritten


class TestEndToEndScriptInterpreter:
    """The full FOR-loop body executes through the script interpreter."""

    def test_for_loop_over_named_first_struct_array(self) -> None:
        """The script-interpreter path that the conformance fixture exercises."""

        async def _run() -> None:
            settings = Settings(persistence_mode="ephemeral")
            engine = DuckDBEngine(settings)
            await engine.start()
            try:
                catalog = MemoryCatalogRepository()
                now = datetime(2026, 5, 19, tzinfo=UTC)
                catalog.create_dataset(
                    DatasetMeta(
                        project_id="p",
                        dataset_id="ds",
                        creation_time=now,
                        last_modified_time=now,
                        etag='"d"',
                    ),
                )
                engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
                ctx = AppContext(
                    settings=settings,
                    clock=FrozenClock(now),
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
                    row_access=RowAccessPolicyManager(
                        catalog=catalog,
                        clock=FrozenClock(),
                    ),
                )
                interpreter = ScriptInterpreter(ctx, "p")
                script = (
                    "CREATE OR REPLACE TABLE `p.ds.acc` "
                    "(label STRING, doubled INT64);\n"
                    "FOR row IN ("
                    "SELECT label, value FROM UNNEST(["
                    "STRUCT('a' AS label, 1 AS value), "
                    "STRUCT('b', 2), "
                    "STRUCT('c', 3)"
                    "]) ORDER BY label"
                    ") DO\n"
                    "  INSERT INTO `p.ds.acc` (label, doubled) "
                    "VALUES (row.label, row.value * 2);\n"
                    "END FOR;\n"
                    "SELECT label, doubled FROM `p.ds.acc` ORDER BY label"
                )
                result = await interpreter.run(script)
                assert result.final_table is not None
                rows = result.final_table.to_pylist()
                assert rows == [
                    {"label": "a", "doubled": 2},
                    {"label": "b", "doubled": 4},
                    {"label": "c", "doubled": 6},
                ]
            finally:
                await engine.stop()

        asyncio.run(_run())
