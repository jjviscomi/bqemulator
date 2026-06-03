"""Unit tests for the scripting interpreter.

These tests use an in-memory DuckDB engine + MemoryCatalogRepository
so they exercise the full SQL pipeline without spinning up the REST
server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import InvalidQueryError, QuotaExceededError
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.scripting.interpreter import (
    _classify_txn_statement,
    _dml_targets,
    run_script,
)
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
    # Mirror the production datasets route — DuckDB schema is the
    # storage-side counterpart to the catalog's DatasetMeta.
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


class TestDeclareSet:
    async def test_declare_and_select(self, ctx: AppContext) -> None:
        result = await run_script(ctx, "p", "DECLARE x INT64 DEFAULT 42; SELECT x;")
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [42]

    async def test_declare_without_default(self, ctx: AppContext) -> None:
        result = await run_script(ctx, "p", "DECLARE x INT64; SELECT x;")
        assert result.final_table is not None

    async def test_set_existing(self, ctx: AppContext) -> None:
        result = await run_script(
            ctx,
            "p",
            "DECLARE x INT64 DEFAULT 1; SET x = x + 10; SELECT x;",
        )
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [11]

    async def test_set_unknown_variable_raises(self, ctx: AppContext) -> None:
        with pytest.raises(InvalidQueryError):
            await run_script(ctx, "p", "SET missing = 1;")

    async def test_declare_string_default_null_preserves_type(self, ctx: AppContext) -> None:
        """``DECLARE x STRING DEFAULT NULL; SELECT x`` surfaces STRING type.

        P8.b closure: without the CAST-on-NULL substitution wrapper,
        the placeholder bound a Python ``None`` which the DuckDB
        driver typed as INT64, surfacing the column as INTEGER even
        though the script's declared type was STRING. The
        ``_rewrite_with_placeholders`` helper now wraps NULL-valued
        placeholders in ``CAST(... AS <declared_type>)`` so the
        wire-format schema reflects the declared type.
        """
        result = await run_script(ctx, "p", "DECLARE x STRING DEFAULT NULL; SELECT x AS s;")
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [None]
        # The schema's first column must surface as STRING — not INT.
        schema = result.final_table.schema
        assert str(schema.field(0).type) in {"string", "large_string"}

    async def test_declare_date_default_null_preserves_type(self, ctx: AppContext) -> None:
        """``DECLARE x DATE DEFAULT NULL`` surfaces DATE, not INT64."""
        result = await run_script(ctx, "p", "DECLARE d DATE DEFAULT NULL; SELECT d AS dv;")
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [None]
        schema = result.final_table.schema
        # DATE → arrow ``date32`` (or similar) — not int64
        assert str(schema.field(0).type).startswith(("date", "timestamp"))

    async def test_declare_int_default_null_preserves_type(self, ctx: AppContext) -> None:
        """``DECLARE x INT64 DEFAULT NULL`` already surfaced INT64 — sanity."""
        result = await run_script(ctx, "p", "DECLARE n INT64 DEFAULT NULL; SELECT n AS nv;")
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [None]
        schema = result.final_table.schema
        assert "int" in str(schema.field(0).type).lower()


class TestControlFlow:
    async def test_if_true_branch(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64 DEFAULT 5;
DECLARE result_val STRING DEFAULT 'unset';
IF x > 0 THEN SET result_val = 'pos'; ELSE SET result_val = 'neg'; END IF;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == ["pos"]

    async def test_if_else_branch(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64 DEFAULT -1;
DECLARE result_val STRING;
IF x > 0 THEN SET result_val = 'pos'; ELSE SET result_val = 'neg'; END IF;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == ["neg"]

    async def test_elseif(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64 DEFAULT 0;
DECLARE result_val STRING;
IF x > 0 THEN SET result_val = 'pos';
ELSEIF x < 0 THEN SET result_val = 'neg';
ELSE SET result_val = 'zero';
END IF;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == ["zero"]

    async def test_while_loop(self, ctx: AppContext) -> None:
        script = """
DECLARE counter INT64 DEFAULT 0;
WHILE counter < 3 DO
  SET counter = counter + 1;
END WHILE;
SELECT counter;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == [3]

    async def test_loop_with_break(self, ctx: AppContext) -> None:
        script = """
DECLARE counter INT64 DEFAULT 0;
LOOP
  SET counter = counter + 1;
  IF counter >= 5 THEN BREAK; END IF;
END LOOP;
SELECT counter;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == [5]

    async def test_loop_with_continue(self, ctx: AppContext) -> None:
        script = """
DECLARE i INT64 DEFAULT 0;
DECLARE total INT64 DEFAULT 0;
WHILE i < 5 DO
  SET i = i + 1;
  IF MOD(i, 2) = 0 THEN CONTINUE; END IF;
  SET total = total + i;
END WHILE;
SELECT total;
"""
        result = await run_script(ctx, "p", script)
        # Odd numbers 1-5: 1+3+5 = 9
        assert result.final_table.column(0).to_pylist() == [9]

    async def test_for_loop(self, ctx: AppContext) -> None:
        script = """
DECLARE total INT64 DEFAULT 0;
FOR row IN (SELECT x FROM UNNEST([1, 2, 3]) AS x) DO
  SET total = total + row.x;
END FOR;
SELECT total;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == [6]

    async def test_max_loop_iterations(self, ctx: AppContext) -> None:
        # Monkey-patch the settings for this test by creating a custom context.
        # Use a small cap to verify the guard fires.
        script = """
DECLARE i INT64 DEFAULT 0;
WHILE i < 9999999 DO
  SET i = i + 1;
END WHILE;
"""
        # The default cap is 1M which is enough to bail before 9.9M.
        with pytest.raises(QuotaExceededError):
            await run_script(ctx, "p", script)


class TestExceptions:
    async def test_begin_end(self, ctx: AppContext) -> None:
        result = await run_script(
            ctx,
            "p",
            "BEGIN DECLARE x INT64 DEFAULT 5; SELECT x; END;",
        )
        assert result.final_table.column(0).to_pylist() == [5]

    async def test_exception_handler_catches(self, ctx: AppContext) -> None:
        script = """
DECLARE result_val STRING DEFAULT 'unset';
BEGIN
  SELECT CAST('abc' AS INT64);
EXCEPTION WHEN ERROR THEN
  SET result_val = 'caught';
END;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == ["caught"]

    async def test_raise_with_message(self, ctx: AppContext) -> None:
        script = """
DECLARE result_val STRING DEFAULT 'unset';
BEGIN
  RAISE USING MESSAGE = 'synthetic error';
EXCEPTION WHEN ERROR THEN
  SET result_val = 'caught';
END;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == ["caught"]

    async def test_raise_bare_propagates(self, ctx: AppContext) -> None:
        # Without a handler, RAISE should escape the script.
        with pytest.raises(InvalidQueryError):
            await run_script(ctx, "p", "RAISE;")


class TestReturnInProcedure:
    """RETURN outside a procedure becomes a script-level early exit."""

    async def test_return_halts_script(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64 DEFAULT 1;
SELECT x;
RETURN;
SELECT 999;
"""
        # Note: The interpreter raises ReturnSignal which isn't caught
        # at the top level for bare scripts. This is expected behaviour —
        # RETURN only makes sense inside a procedure.
        from bqemulator.scripting.exceptions import ReturnSignal

        with pytest.raises(ReturnSignal):
            await run_script(ctx, "p", script)


class TestExecuteImmediate:
    async def test_basic(self, ctx: AppContext) -> None:
        result = await run_script(
            ctx,
            "p",
            "EXECUTE IMMEDIATE 'SELECT 42 AS v';",
        )
        assert result.final_table.column(0).to_pylist() == [42]

    async def test_using(self, ctx: AppContext) -> None:
        script = """
DECLARE result_val INT64;
EXECUTE IMMEDIATE 'SELECT ? * 2' INTO result_val USING 21;
SELECT result_val;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.column(0).to_pylist() == [42]

    async def test_into_multi_row_raises(self, ctx: AppContext) -> None:
        script = """
DECLARE x INT64;
EXECUTE IMMEDIATE 'SELECT * FROM UNNEST([1, 2]) AS x' INTO x;
"""
        with pytest.raises(InvalidQueryError, match="multiple rows"):
            await run_script(ctx, "p", script)


class TestProjectionNameInference:
    """ADR 0023 §1.E — preserve BigQuery's column-name inference rule.

    BigQuery names a single-identifier projection after the source
    identifier (``SELECT label`` → column name ``label``). The
    scripting interpreter rewrites bare variable references to bound
    parameters, which erases the original name; the interpreter must
    propagate it as an alias so DuckDB doesn't return ``$1``.
    """

    async def test_bare_variable_projection_keeps_identifier_name(
        self,
        ctx: AppContext,
    ) -> None:
        script = """
DECLARE label STRING DEFAULT 'big';
SELECT label;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.schema.names == ["label"]
        assert result.final_table.column(0).to_pylist() == ["big"]

    async def test_explicit_alias_is_preserved(self, ctx: AppContext) -> None:
        """An explicit AS must win over the inferred name."""
        script = """
DECLARE label STRING DEFAULT 'big';
SELECT label AS renamed;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.schema.names == ["renamed"]

    async def test_complex_expression_does_not_inherit_name(
        self,
        ctx: AppContext,
    ) -> None:
        """Inference only applies to a single identifier — not arithmetic."""
        script = """
DECLARE n INT64 DEFAULT 10;
SELECT n + 1;
"""
        result = await run_script(ctx, "p", script)
        # BigQuery names a computed column ``f0_`` (or similar);
        # the contract here is just that the bare ``n`` name is NOT
        # propagated to a column that is the result of an expression.
        assert result.final_table.schema.names != ["n"]
        assert result.final_table.column(0).to_pylist() == [11]

    async def test_multi_projection_each_bare_var_named(
        self,
        ctx: AppContext,
    ) -> None:
        script = """
DECLARE a INT64 DEFAULT 1;
DECLARE b STRING DEFAULT 'two';
SELECT a, b;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table.schema.names == ["a", "b"]
        assert result.final_table.column(0).to_pylist() == [1]
        assert result.final_table.column(1).to_pylist() == ["two"]


class TestLastStatementWins:
    """ADR 0023 §1.F — the script result is the *last* statement's result.

    BigQuery returns the result set of the script's final statement. A
    SELECT / WITH / set-op contributes its rows; a DDL / DML / transaction-
    control statement has no result set, so a script ending in one returns
    an empty result (``final_table=None``) — even when an earlier statement
    produced rows. The scripting interpreter must match this.
    """

    async def test_create_table_then_select_returns_only_select_rows(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE TABLE foo AS …; SELECT * FROM foo`` returns the SELECT only."""
        script = """
CREATE OR REPLACE TABLE `p.ds.t_last_stmt` AS
  SELECT 1 AS id, 'a' AS label UNION ALL
  SELECT 2, 'b' UNION ALL
  SELECT 3, 'c';
SELECT id, label FROM `p.ds.t_last_stmt` ORDER BY id;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.schema.names == ["id", "label"]
        assert result.final_table.column(0).to_pylist() == [1, 2, 3]
        assert result.final_table.column(1).to_pylist() == ["a", "b", "c"]
        # Plain ``CREATE TABLE`` is auto-registered in the catalog so
        # downstream catalog-aware paths (versioning managers,
        # INFORMATION_SCHEMA) find the freshly-created table.
        assert ctx.catalog.get_table("p", "ds", "t_last_stmt") is not None

    async def test_multi_select_returns_last_select(self, ctx: AppContext) -> None:
        """Two SELECTs: the script result is the *last* one's rows."""
        result = await run_script(ctx, "p", "SELECT 1 AS a;\nSELECT 2 AS b;")
        assert result.final_table is not None
        assert result.final_table.schema.names == ["b"]
        assert result.final_table.column(0).to_pylist() == [2]

    async def test_select_then_ddl_returns_empty(self, ctx: AppContext) -> None:
        """A SELECT followed by a trailing DDL yields an empty result.

        Regression: before the last-statement-wins fix the interpreter kept
        the prior SELECT's rows, but BigQuery returns the *final* statement's
        result and DDL has none.
        """
        script = "SELECT 1 AS a;\nCREATE TABLE `p.ds.t_trailing_ddl` (id INT64);"
        result = await run_script(ctx, "p", script)
        assert result.final_table is None
        # The DDL still executed.
        assert ctx.catalog.get_table("p", "ds", "t_trailing_ddl") is not None

    async def test_select_then_drop_returns_empty(self, ctx: AppContext) -> None:
        """A SELECT followed by a trailing DROP yields an empty result."""
        script = (
            "CREATE OR REPLACE TABLE `p.ds.t_drop_last` AS SELECT 1 AS id;\n"
            "SELECT id FROM `p.ds.t_drop_last`;\n"
            "DROP TABLE `p.ds.t_drop_last`;"
        )
        result = await run_script(ctx, "p", script)
        assert result.final_table is None
        # The DROP still executed.
        assert ctx.catalog.get_table("p", "ds", "t_drop_last") is None

    async def test_select_then_dml_returns_empty(self, ctx: AppContext) -> None:
        """A SELECT followed by a trailing DML yields an empty result."""
        script = (
            "CREATE OR REPLACE TABLE `p.ds.t_dml_last` AS SELECT 1 AS id;\n"
            "SELECT id FROM `p.ds.t_dml_last`;\n"
            "INSERT INTO `p.ds.t_dml_last` VALUES (2);"
        )
        result = await run_script(ctx, "p", script)
        assert result.final_table is None

    async def test_select_then_transaction_control_returns_empty(
        self,
        ctx: AppContext,
    ) -> None:
        """A SELECT followed by a trailing COMMIT yields an empty result."""
        script = "BEGIN TRANSACTION;\nSELECT 1 AS a;\nCOMMIT;"
        result = await run_script(ctx, "p", script)
        assert result.final_table is None

    async def test_call_proc_ending_in_ddl_returns_empty(self, ctx: AppContext) -> None:
        """A CALL whose procedure ends in DDL returns an empty result.

        The procedure produces no result set, so per last-statement-wins the
        CALL (the script's final statement) must reset ``_final_table`` to
        empty rather than leak the pre-CALL SELECT's rows.
        """
        script = (
            "CREATE PROCEDURE `p.ds.p_only_ddl`()\n"
            "BEGIN\n"
            "  CREATE OR REPLACE TABLE `p.ds.from_proc` (id INT64);\n"
            "END;\n"
            "SELECT 1 AS a;\n"
            "CALL `p.ds.p_only_ddl`();"
        )
        result = await run_script(ctx, "p", script)
        assert result.final_table is None

    async def test_execute_immediate_ddl_returns_empty(self, ctx: AppContext) -> None:
        """A trailing ``EXECUTE IMMEDIATE`` of DDL returns an empty result."""
        script = (
            "SELECT 1 AS a;\nEXECUTE IMMEDIATE 'CREATE OR REPLACE TABLE `p.ds.ei_tbl` (id INT64)';"
        )
        result = await run_script(ctx, "p", script)
        assert result.final_table is None

    async def test_call_refresh_mv_last_returns_empty(self, ctx: AppContext) -> None:
        """A trailing ``CALL BQ.REFRESH_MATERIALIZED_VIEW`` returns an empty result.

        The builtin refresh CALL dispatches through
        ``_exec_call_refresh_mv`` (not ``_invoke_procedure``) and produces
        no result set, so per last-statement-wins it must reset
        ``_final_table`` rather than leak the prior SELECT's rows.
        """
        script = """
CREATE OR REPLACE TABLE `p.ds.refresh_base` AS SELECT 1 AS x;
CREATE MATERIALIZED VIEW `p.ds.refresh_mv`
AS SELECT COUNT(*) AS n FROM `p.ds.refresh_base`;
SELECT 1 AS a;
CALL BQ.REFRESH_MATERIALIZED_VIEW('p.ds.refresh_mv');
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is None

    async def test_ddl_only_script_returns_no_rows(
        self,
        ctx: AppContext,
    ) -> None:
        """A script that only runs DDL returns ``final_table=None``."""
        script = """
CREATE OR REPLACE TABLE `p.ds.t_ddl_only` AS
  SELECT 1 AS id;
"""
        result = await run_script(ctx, "p", script)
        # DDL ran successfully but emitted no rows.
        assert result.final_table is None
        assert ctx.catalog.get_table("p", "ds", "t_ddl_only") is not None

    async def test_versioning_ddl_inside_script_routes_to_manager(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE SNAPSHOT TABLE`` inside a script reaches the manager.

        DuckDB does not parse ``CREATE SNAPSHOT TABLE`` — without
        per-statement dispatch the script would crash on the DDL.
        The interpreter must route versioning DDL through
        ``execute_versioning_ddl`` so the snapshot manager handles it
        and the trailing SELECT still returns rows from the snapshot.
        """
        script = """
CREATE OR REPLACE TABLE `p.ds.t_snap_source` AS
  SELECT 1 AS id, 'x' AS label UNION ALL
  SELECT 2, 'y';
CREATE SNAPSHOT TABLE `p.ds.snap_v1` CLONE `p.ds.t_snap_source`;
SELECT id, label FROM `p.ds.snap_v1` ORDER BY id;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [1, 2]
        assert result.final_table.column(1).to_pylist() == ["x", "y"]
        snap_meta = ctx.catalog.get_table("p", "ds", "snap_v1")
        assert snap_meta is not None
        assert snap_meta.table_type == "SNAPSHOT"

    async def test_clone_ddl_inside_script_routes_to_manager(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE TABLE … CLONE`` inside a script reaches the clone manager."""
        script = """
CREATE OR REPLACE TABLE `p.ds.t_clone_source` AS
  SELECT 10 AS id, 'p' AS label;
CREATE OR REPLACE TABLE `p.ds.clone_v1` CLONE `p.ds.t_clone_source`;
SELECT id, label FROM `p.ds.clone_v1`;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [10]
        clone_meta = ctx.catalog.get_table("p", "ds", "clone_v1")
        assert clone_meta is not None
        assert clone_meta.table_type == "CLONE"

    async def test_mv_ddl_inside_script_routes_to_manager(
        self,
        ctx: AppContext,
    ) -> None:
        """``CREATE MATERIALIZED VIEW`` inside a script reaches the MV manager."""
        script = """
CREATE OR REPLACE TABLE `p.ds.t_mv_source` AS
  SELECT 'a' AS label UNION ALL
  SELECT 'a' UNION ALL
  SELECT 'b';
CREATE MATERIALIZED VIEW `p.ds.mv_v1`
AS SELECT label, COUNT(*) AS n FROM `p.ds.t_mv_source` GROUP BY label;
SELECT label, n FROM `p.ds.mv_v1` ORDER BY label;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.schema.names == ["label", "n"]
        # Two distinct labels: 'a' (n=2) and 'b' (n=1).
        rows = sorted(
            zip(
                result.final_table.column(0).to_pylist(),
                result.final_table.column(1).to_pylist(),
                strict=True,
            ),
        )
        assert rows == [("a", 2), ("b", 1)]
        mv_meta = ctx.catalog.get_table("p", "ds", "mv_v1")
        assert mv_meta is not None
        assert mv_meta.table_type == "MATERIALIZED_VIEW"


class TestCallBqRefreshMaterializedView:
    """``CALL BQ.REFRESH_MATERIALIZED_VIEW`` routes through the MV manager."""

    async def test_call_form_refreshes_mv(self, ctx: AppContext) -> None:
        script = """
CREATE OR REPLACE TABLE `p.ds.base` AS
  SELECT 'a' AS label UNION ALL
  SELECT 'a' UNION ALL
  SELECT 'b';
CREATE MATERIALIZED VIEW `p.ds.mv`
AS SELECT label, COUNT(*) AS n FROM `p.ds.base` GROUP BY label;
INSERT INTO `p.ds.base` VALUES ('a'), ('b'), ('c');
CALL BQ.REFRESH_MATERIALIZED_VIEW('p.ds.mv');
SELECT label, n FROM `p.ds.mv` ORDER BY label;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        rows = sorted(
            zip(
                result.final_table.column(0).to_pylist(),
                result.final_table.column(1).to_pylist(),
                strict=True,
            ),
        )
        # After refresh: 'a' appears 3 times, 'b' twice, 'c' once.
        assert rows == [("a", 3), ("b", 2), ("c", 1)]

    async def test_call_form_lowercase(self, ctx: AppContext) -> None:
        """Routine ref comparison is case-insensitive."""
        script = """
CREATE OR REPLACE TABLE `p.ds.t` AS SELECT 1 AS x;
CREATE MATERIALIZED VIEW `p.ds.m`
AS SELECT COUNT(*) AS n FROM `p.ds.t`;
call bq.refresh_materialized_view('p.ds.m');
SELECT n FROM `p.ds.m`;
"""
        result = await run_script(ctx, "p", script)
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [1]


class TestClassifyTxnStatement:
    """``_classify_txn_statement`` recognises BQ transaction-control SQL."""

    @pytest.mark.parametrize(
        "sql",
        [
            "BEGIN",
            "BEGIN;",
            "BEGIN TRANSACTION",
            "BEGIN TRANSACTION;",
            "  begin   transaction  ;",
            "START TRANSACTION",
            "Start Transaction;",
        ],
    )
    def test_begin_forms(self, sql: str) -> None:
        assert _classify_txn_statement(sql) == "BEGIN"

    @pytest.mark.parametrize(
        "sql",
        [
            "COMMIT",
            "COMMIT TRANSACTION",
            "COMMIT WORK",
            "commit;",
            "END TRANSACTION",
        ],
    )
    def test_commit_forms(self, sql: str) -> None:
        assert _classify_txn_statement(sql) == "COMMIT"

    @pytest.mark.parametrize(
        "sql",
        ["ROLLBACK", "ROLLBACK TRANSACTION", "ROLLBACK WORK", "  rollback  ;"],
    )
    def test_rollback_forms(self, sql: str) -> None:
        assert _classify_txn_statement(sql) == "ROLLBACK"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "INSERT INTO t VALUES (1)",
            "BEGIN SELECT 1; END",  # BEGIN/END block, not a transaction
            "ROLLBACK TO SAVEPOINT sp",  # not supported, not a plain ROLLBACK
            "BEGINNING",  # word that starts with BEGIN
        ],
    )
    def test_other_sql_is_not_txn_control(self, sql: str) -> None:
        assert _classify_txn_statement(sql) is None


class TestDmlTargets:
    """``_dml_targets`` returns quoted DuckDB-form targets for DML statements."""

    def test_insert_two_part_uses_project_id(self) -> None:
        assert _dml_targets("INSERT INTO ds.t VALUES (1)", "my-project") == ['"my-project__ds"."t"']

    def test_insert_three_part_uses_catalog(self) -> None:
        assert _dml_targets("INSERT INTO acme.ds.t VALUES (1)", "ignored") == ['"acme__ds"."t"']

    def test_insert_backticked_compound(self) -> None:
        assert _dml_targets("INSERT INTO `acme.ds`.t VALUES (1)", "ignored") == ['"acme__ds"."t"']

    def test_update(self) -> None:
        assert _dml_targets("UPDATE ds.t SET x = 1 WHERE id = 1", "p") == ['"p__ds"."t"']

    def test_delete(self) -> None:
        assert _dml_targets("DELETE FROM ds.t WHERE id = 1", "p") == ['"p__ds"."t"']

    def test_merge_returns_target_only(self) -> None:
        # MERGE references a source table too; only the target is
        # returned for snapshotting (the source is read-only).
        result = _dml_targets(
            "MERGE INTO ds.target USING ds.source ON target.id = source.id "
            "WHEN MATCHED THEN UPDATE SET x = 1",
            "p",
        )
        assert result == ['"p__ds"."target"']

    def test_select_returns_empty(self) -> None:
        assert _dml_targets("SELECT * FROM ds.t", "p") == []

    def test_unparseable_returns_empty(self) -> None:
        assert _dml_targets("NOT VALID SQL %%%", "p") == []

    def test_bare_table_returns_empty(self) -> None:
        # No dataset qualifier — caller has no way to determine the
        # target schema, so we skip the snapshot rather than guess.
        assert _dml_targets("INSERT INTO bare_table VALUES (1)", "p") == []


class TestTransactionShim:
    """End-to-end BEGIN/COMMIT/ROLLBACK semantics in the script interpreter."""

    async def test_commit_keeps_changes(self, ctx: AppContext) -> None:
        result = await run_script(
            ctx,
            "p",
            """
CREATE OR REPLACE TABLE `p.ds.ledger` (id INT64, amount INT64);
INSERT INTO `p.ds.ledger` (id, amount) VALUES (1, 100);
BEGIN TRANSACTION;
INSERT INTO `p.ds.ledger` (id, amount) VALUES (2, 200);
INSERT INTO `p.ds.ledger` (id, amount) VALUES (3, 300);
COMMIT TRANSACTION;
SELECT COUNT(*) AS n FROM `p.ds.ledger`;
""",
        )
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [3]

    async def test_rollback_undoes_changes(self, ctx: AppContext) -> None:
        result = await run_script(
            ctx,
            "p",
            """
CREATE OR REPLACE TABLE `p.ds.ledger` (id INT64, amount INT64);
INSERT INTO `p.ds.ledger` (id, amount) VALUES (1, 100);
BEGIN TRANSACTION;
INSERT INTO `p.ds.ledger` (id, amount) VALUES (2, 200);
INSERT INTO `p.ds.ledger` (id, amount) VALUES (3, 300);
ROLLBACK TRANSACTION;
SELECT COUNT(*) AS n FROM `p.ds.ledger`;
""",
        )
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [1]

    async def test_exception_handler_preserves_dml(self, ctx: AppContext) -> None:
        """BigQuery's EXCEPTION semantic: DML inside BEGIN TRANSACTION
        survives a caught error and commits at end-of-script.

        Reproduces the
        ``routines_scripting/txn_in_exception_block`` conformance fixture
        at the unit level.
        """
        result = await run_script(
            ctx,
            "p",
            """
CREATE OR REPLACE TABLE `p.ds.ledger` (id INT64, amount INT64);
BEGIN
  BEGIN TRANSACTION;
  INSERT INTO `p.ds.ledger` (id, amount) VALUES (1, 100);
  EXECUTE IMMEDIATE 'SELECT 1 / 0';
  COMMIT TRANSACTION;
EXCEPTION WHEN ERROR THEN
  SELECT 'caught' AS outcome;
END;
SELECT COUNT(*) AS n FROM `p.ds.ledger`;
""",
        )
        assert result.final_table is not None
        # DML survives the handler — matches BQ recording.
        assert result.final_table.column(0).to_pylist() == [1]

    async def test_implicit_transaction(self, ctx: AppContext) -> None:
        """Multi-statement script without BEGIN auto-commits each."""
        result = await run_script(
            ctx,
            "p",
            """
CREATE OR REPLACE TABLE `p.ds.ledger` (id INT64);
INSERT INTO `p.ds.ledger` (id) VALUES (1);
INSERT INTO `p.ds.ledger` (id) VALUES (2);
SELECT COUNT(*) AS n FROM `p.ds.ledger`;
""",
        )
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [2]

    async def test_unhandled_error_rolls_back(self, ctx: AppContext) -> None:
        """An unhandled error inside a transaction rolls back the DML."""
        await run_script(
            ctx,
            "p",
            "CREATE OR REPLACE TABLE `p.ds.ledger` (id INT64);"
            "INSERT INTO `p.ds.ledger` (id) VALUES (1);",
        )
        # The script raises — DML inside the txn should NOT persist.
        with pytest.raises(InvalidQueryError):
            await run_script(
                ctx,
                "p",
                """
BEGIN TRANSACTION;
INSERT INTO `p.ds.ledger` (id) VALUES (2);
SELECT 1 / 0;
""",
            )
        # Verify only the pre-transaction row survives.
        result = await run_script(
            ctx,
            "p",
            "SELECT COUNT(*) AS n FROM `p.ds.ledger`;",
        )
        assert result.final_table is not None
        assert result.final_table.column(0).to_pylist() == [1]
