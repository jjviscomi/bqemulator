"""Tests for the Phase 9 pre-translator rewriter."""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.sql.rewriter.specialized_types import rewrite_specialized_types


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


class TestIntervalLiteralRewrite:
    def test_year_to_second(self) -> None:
        sql = "SELECT INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND"
        out = rewrite_specialized_types(sql)
        # Should now contain single-unit interval pieces.
        assert "YEAR TO SECOND" not in out
        assert "INTERVAL '1' YEAR" in out
        assert "INTERVAL '2' MONTH" in out
        assert "INTERVAL '6.789' SECOND" in out

    def test_year_to_month(self) -> None:
        sql = "SELECT INTERVAL '1-2' YEAR TO MONTH"
        out = rewrite_specialized_types(sql)
        assert "YEAR TO MONTH" not in out
        assert "INTERVAL '1' YEAR" in out
        assert "INTERVAL '2' MONTH" in out

    def test_single_unit_passes_through(self) -> None:
        sql = "SELECT INTERVAL '36' HOUR"
        assert rewrite_specialized_types(sql) == sql

    def test_short_circuits_when_no_interval(self) -> None:
        sql = "SELECT 1 + 1"
        assert rewrite_specialized_types(sql) is sql

    def test_invalid_sql_returns_unchanged(self) -> None:
        # If we can't even parse the BQ source we should return as-is —
        # SQLGlot will surface a clean error downstream.
        sql = "SELECT INTERVAL 'this is not valid' YEAR TO SECOND"
        # parse_one might succeed but parse_interval_literal will fail,
        # so the rewriter leaves the AST untouched and returns the
        # original SQL.
        out = rewrite_specialized_types(sql)
        # Either unchanged (failure path) or rewritten (if SQLGlot
        # somehow handled it). The important invariant: no exception.
        assert isinstance(out, str)


class TestRangeConstructorRewrite:
    def test_rewrites_range_to_struct(self) -> None:
        sql = "SELECT RANGE(DATE '2024-01-01', DATE '2024-12-31')"
        out = rewrite_specialized_types(sql)
        upper = out.upper()
        assert "STRUCT" in upper
        # The struct literal carries the standard ``start`` / ``end`` fields.
        assert "start" in out.lower()
        assert "end" in out.lower()

    def test_generate_array_not_rewritten(self) -> None:
        sql = "SELECT GENERATE_ARRAY(1, 5)"
        out = rewrite_specialized_types(sql)
        # GENERATE_ARRAY's call shape must not be confused with RANGE().
        assert "GENERATE_ARRAY" in out.upper()

    def test_nested_range_in_function(self) -> None:
        sql = (
            "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-06-15')"
        )
        out = rewrite_specialized_types(sql)
        # Inner RANGE got rewritten; outer RANGE_CONTAINS preserved.
        assert "RANGE_CONTAINS" in out.upper()
        assert "STRUCT" in out.upper()


class TestRangeLiteralRewrite:
    """ADR 0023 §1.G: ``RANGE<T> '[start, end)'`` typed literals."""

    def test_date_literal_rewrites_to_struct(self) -> None:
        sql = "SELECT RANGE<DATE> '[2024-01-01, 2024-01-31)' AS r"
        out = rewrite_specialized_types(sql)
        upper = out.upper()
        assert "RANGE<DATE>" not in upper
        assert "STRUCT" in upper
        assert "AS DATE" in upper
        assert "2024-01-01" in out
        assert "2024-01-31" in out

    def test_datetime_literal_rewrites_to_naive_timestamp_cast(self) -> None:
        sql = "SELECT RANGE<DATETIME> '[2024-01-01 00:00:00, 2024-02-01 00:00:00)' AS r"
        out = rewrite_specialized_types(sql)
        # BigQuery DATETIME on the wire → SQLGlot serialises ``DATETIME``
        # which transpiles to DuckDB ``TIMESTAMP`` (naive).
        assert "AS DATETIME" in out.upper()

    def test_timestamp_literal_rewrites_to_tz_cast(self) -> None:
        sql = "SELECT RANGE<TIMESTAMP> '[2024-01-01 00:00:00+00, 2024-02-01 00:00:00+00)' AS r"
        out = rewrite_specialized_types(sql)
        # BigQuery TIMESTAMP is TZ-aware; serialises as ``TIMESTAMP`` in
        # the BQ dialect and transpiles to DuckDB ``TIMESTAMPTZ``.
        assert "AS TIMESTAMP" in out.upper()

    def test_unbounded_start_becomes_null_cast(self) -> None:
        sql = "SELECT RANGE<DATE> '[UNBOUNDED, 2024-01-31)' AS r"
        out = rewrite_specialized_types(sql)
        assert "CAST(NULL AS DATE)" in out.upper()
        assert "2024-01-31" in out

    def test_unbounded_end_becomes_null_cast(self) -> None:
        sql = "SELECT RANGE<DATE> '[2024-01-01, UNBOUNDED)' AS r"
        out = rewrite_specialized_types(sql)
        assert "CAST(NULL AS DATE)" in out.upper()
        assert "2024-01-01" in out

    def test_array_of_range_literals_rewrites_each(self) -> None:
        sql = (
            "SELECT [RANGE<DATE> '[2024-01-01, 2024-02-01)', "
            "RANGE<DATE> '[2024-02-01, 2024-03-01)'] AS ranges"
        )
        out = rewrite_specialized_types(sql)
        assert out.upper().count("STRUCT") >= 2
        assert "RANGE<DATE>" not in out.upper()

    def test_range_literal_inside_function_call(self) -> None:
        sql = (
            "SELECT GENERATE_RANGE_ARRAY("
            "RANGE<DATE> '[2024-01-01, 2024-01-08)', INTERVAL 2 DAY) AS r"
        )
        out = rewrite_specialized_types(sql)
        assert "GENERATE_RANGE_ARRAY" in out.upper()
        assert "STRUCT" in out.upper()
        assert "RANGE<DATE>" not in out.upper()


class TestRangeDataTypeRewrite:
    """Column-definition / non-literal-CAST ``RANGE<T>`` references.

    The literal-cast pass (``_rewrite_range_literals``) replaces every
    ``Cast(Literal, RANGE<T>)`` node wholesale, so the only
    ``DataType.RANGE`` nodes that survive to the data-type pass are
    column declarations (``CREATE TABLE t (col RANGE<T>)``) and the
    type slot of non-literal ``CAST`` (``CAST(x AS RANGE<T>)``).
    DuckDB rejects ``RANGE(T)`` as a type — without this pass the
    setup phase of the conformance fixtures would fail at
    ``CREATE TABLE``.
    """

    def test_date_column_rewrites_to_struct(self) -> None:
        sql = "CREATE OR REPLACE TABLE t (col RANGE<DATE>)"
        out = rewrite_specialized_types(sql)
        upper = out.upper()
        assert "RANGE<DATE>" not in upper
        # The serialized BigQuery form uses backticks; the canonical
        # field names ``start`` / ``end`` always land in the rewrite.
        assert "STRUCT" in upper
        assert "DATE" in upper

    def test_datetime_column_rewrites_to_struct(self) -> None:
        sql = "CREATE OR REPLACE TABLE t (col RANGE<DATETIME>)"
        out = rewrite_specialized_types(sql)
        upper = out.upper()
        assert "RANGE<DATETIME>" not in upper
        assert "DATETIME" in upper

    def test_timestamp_column_rewrites_to_struct(self) -> None:
        sql = "CREATE OR REPLACE TABLE t (col RANGE<TIMESTAMP>)"
        out = rewrite_specialized_types(sql)
        upper = out.upper()
        assert "RANGE<TIMESTAMP>" not in upper
        assert "TIMESTAMP" in upper

    def test_non_literal_cast_rewrites_target_type(self) -> None:
        sql = "SELECT CAST(x AS RANGE<DATE>) FROM t"
        out = rewrite_specialized_types(sql)
        assert "RANGE<DATE>" not in out.upper()
        assert "STRUCT" in out.upper()

    def test_table_with_range_column_translates_to_valid_duckdb(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # End-to-end: the rewritten DuckDB SQL must execute cleanly
        # against a real DuckDB connection.
        import sqlglot

        sql = "CREATE OR REPLACE TABLE bqemu_range_test (col RANGE<DATE>)"
        rewritten = rewrite_specialized_types(sql)
        duck_sql = sqlglot.transpile(rewritten, read="bigquery", write="duckdb", pretty=False)[0]
        conn.execute(duck_sql)
        # Confirm the column landed on the canonical RANGE shape.
        info = conn.execute(
            "SELECT column_type FROM (DESCRIBE bqemu_range_test) WHERE column_name = 'col'"
        ).fetchone()
        assert info is not None
        assert "STRUCT" in info[0].upper()
        assert '"start"' in info[0].lower() or "start " in info[0].lower()
        assert '"end"' in info[0].lower() or "end " in info[0].lower()


def test_no_interval_no_range_short_circuits() -> None:
    sql = "SELECT 1, 2, 3"
    assert rewrite_specialized_types(sql) is sql


class TestEdgeCases:
    def test_unparseable_sql_returns_unchanged(self) -> None:
        # Triggers the ``except ParseError`` in rewrite_specialized_types.
        sql = "INTERVAL '1' TO !!!  not parseable !!!"
        out = rewrite_specialized_types(sql)
        # We tolerate the parse failure and return the input string.
        assert out == sql

    def test_one_arg_range_not_rewritten(self) -> None:
        # ``RANGE(x)`` (1 arg) is not the constructor — leave it alone.
        sql = "SELECT RANGE(5)"
        out = rewrite_specialized_types(sql)
        assert "STRUCT" not in out.upper()

    def test_three_arg_range_not_rewritten(self) -> None:
        # ``RANGE(x, y, z)`` (3 arg) is the generate-series form.
        sql = "SELECT RANGE(1, 5, 1)"
        out = rewrite_specialized_types(sql)
        assert "STRUCT" not in out.upper()

    def test_interval_with_non_literal_value_skipped(self) -> None:
        # Compound interval with a column ref — we skip the rewrite
        # because the parser hits the ``not Literal`` early-return.
        # Result: SQLGlot transpile downstream may still fail, but
        # the rewriter itself does not raise.
        sql = "SELECT INTERVAL my_col YEAR TO MONTH"
        out = rewrite_specialized_types(sql)
        # Either unchanged or some safe rewrite — must not raise.
        assert isinstance(out, str)

    def test_interval_literal_with_unparseable_content_left_alone(self) -> None:
        # The literal is a string but doesn't match the expected
        # span shape — parse_interval_literal raises, we leave it alone.
        sql = "SELECT INTERVAL 'garbage' YEAR TO SECOND"
        out = rewrite_specialized_types(sql)
        # No rewrite happens; the input is returned (or unchanged
        # bigquery-dialect re-emission, depending on parser state).
        assert "YEAR TO SECOND" in out.upper() or out == sql


class TestVarNameExtraction:
    def test_var_name_from_identifier(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rewriter.specialized_types import _var_name

        assert _var_name(exp.Identifier(this="day")) == "DAY"

    def test_var_name_from_literal(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rewriter.specialized_types import _var_name

        assert _var_name(exp.Literal.string("MINUTE")) == "MINUTE"

    def test_var_name_returns_none_for_other_node(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rewriter.specialized_types import _var_name

        assert _var_name(exp.Column(this=exp.Identifier(this="x"))) is None


class TestIntervalSpanText:
    def test_returns_none_when_end_missing(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rewriter.specialized_types import _interval_span_text

        span = exp.IntervalSpan(this=exp.Var(this="YEAR"), expression=None)
        assert _interval_span_text(span) is None
