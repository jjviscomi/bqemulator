"""Tests for the strict division-by-zero pre-translator (scope-expansion #17).

The rewrite in :mod:`bqemulator.sql.rewriter.division_by_zero` wraps
every bare ``/`` operator (``exp.Div`` AST node) in a
``CASE WHEN divisor = 0 THEN error('Division by zero') ELSE a/b END``
so a runtime ``a / 0`` raises ``Invalid Input Error`` — matching
BigQuery's ``OUT_OF_RANGE: Division by zero`` semantic. The
script interpreter's ``BEGIN ... EXCEPTION WHEN ERROR THEN ... END``
block then catches the raise.

The walk leaves a ``Div`` alone when the divisor is a non-zero numeric
literal (the cheap ``a / 2`` optimisation that keeps the AST simple
for the common divide-by-constant case).

The negative guards for ``SAFE_DIVIDE`` and ``IEEE_DIVIDE`` are
implicit — both function-call forms are opaque ``Anonymous`` / typed
nodes at the BigQuery AST level when our pre-translator runs. Their
internal ``Div`` shapes are produced *after* the pre-translator (by
SQLGlot's transpile and the Bucket J ``IeeeDivideRule``,
respectively), so the walk never sees them.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.division_by_zero import rewrite_division_by_zero
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    """A fresh translator with all production rules wired in."""
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """A standalone in-memory DuckDB connection."""
    return duckdb.connect()


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    """Translate *sql* and return the first result row (or raise)."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


def _execute_all(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> list[object]:
    """Translate *sql* and return all result rows."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchall()


class TestNoOpSql:
    """``rewrite_division_by_zero`` is a no-op when no ``/`` appears."""

    def test_no_slash_returns_input_unchanged(self) -> None:
        sql = "SELECT 1 + 2"
        assert rewrite_division_by_zero(sql) is sql

    def test_string_with_slash_outside_div_no_op(self) -> None:
        # SQLGlot parses the slash inside the string literal so no Div
        # node appears. The rewriter walks the AST, finds no Div, and
        # returns the input unchanged.
        sql = "SELECT 'a/b'"
        assert rewrite_division_by_zero(sql) == sql


class TestBareDivisionRaises:
    """A bare ``a / 0`` raises ``Invalid Input Error: Division by zero``."""

    def test_integer_one_over_zero_raises(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(t, con, "SELECT 1 / 0")

    def test_float_over_zero_raises(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(t, con, "SELECT 1.0 / 0.0")

    def test_zero_over_zero_raises(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(t, con, "SELECT 0 / 0")

    def test_column_over_zero_column_raises(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(
                t,
                con,
                "WITH t AS (SELECT 5 AS a, 0 AS b) SELECT a / b FROM t",
            )

    def test_non_zero_division_returns_quotient(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT 10 / 4")
        assert row == (2.5,)


class TestConstDivisorOptimization:
    """The wrap is skipped when the divisor is a non-zero numeric literal."""

    @pytest.mark.parametrize(
        "divisor_text",
        ["2", "3.14", "1.0", "-2", "-3.14"],
    )
    def test_non_zero_literal_divisor_unwrapped(self, divisor_text: str) -> None:
        sql = f"SELECT a / {divisor_text} FROM t"
        rewritten = rewrite_division_by_zero(sql)
        # The optimisation skips the wrap, returning the input
        # unchanged.
        assert "CASE WHEN" not in rewritten
        # Sanity: the divisor still appears unchanged in the output.
        assert divisor_text in rewritten

    def test_zero_literal_divisor_is_wrapped(self) -> None:
        # ``a / 0`` MUST wrap — the literal-zero case is the whole
        # point of the rewrite. The const optimisation only fires for
        # non-zero literals.
        sql = "SELECT a / 0 FROM t"
        rewritten = rewrite_division_by_zero(sql)
        assert "CASE WHEN" in rewritten
        assert "error('Division by zero')" in rewritten

    def test_column_divisor_is_wrapped(self) -> None:
        # A non-literal divisor must be wrapped — we cannot statically
        # know whether it is zero.
        sql = "SELECT a / b FROM t"
        rewritten = rewrite_division_by_zero(sql)
        assert "CASE WHEN b = 0" in rewritten

    def test_paren_const_divisor_unwrapped(self) -> None:
        # ``a / (2)`` peels the Paren and sees the underlying literal —
        # skip the wrap.
        sql = "SELECT a / (2) FROM t"
        rewritten = rewrite_division_by_zero(sql)
        assert "CASE WHEN" not in rewritten


class TestSafeDivideUnaffected:
    """``SAFE_DIVIDE(a, b)`` keeps its native ``NULL``-on-zero semantic."""

    def test_safe_divide_by_zero_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT SAFE_DIVIDE(1, 0) AS r")
        assert row == (None,)

    def test_safe_divide_non_zero_returns_quotient(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT SAFE_DIVIDE(10, 2) AS r")
        assert row == (5.0,)

    def test_safe_divide_translation_not_a_div_node(self) -> None:
        # SAFE_DIVIDE is parsed as a function call (Anonymous / typed),
        # not as a Div. Our walk never sees a Div for this form, so the
        # rewrite is a no-op at the AST level (the SQL still changes
        # only via the SQLGlot reserialise round-trip — verify by
        # asserting no CASE appears).
        sql = "SELECT SAFE_DIVIDE(a, b) FROM t"
        rewritten = rewrite_division_by_zero(sql)
        assert "CASE WHEN" not in rewritten


class TestIeeeDivideUnaffected:
    """``IEEE_DIVIDE(a, b)`` keeps its native ``Inf``-on-zero semantic."""

    def test_ieee_divide_by_zero_returns_inf(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(1, 0) AS r")
        assert row == (float("inf"),)

    def test_ieee_divide_float_by_zero_returns_inf(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(1.0, 0.0) AS r")
        assert row == (float("inf"),)

    def test_ieee_divide_non_zero_returns_quotient(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(10, 2) AS r")
        assert row == (5.0,)

    def test_ieee_divide_translation_not_a_div_at_bq_ast(self) -> None:
        # ``IEEE_DIVIDE`` is parsed as Anonymous at the BigQuery AST
        # level; the Div is produced by Bucket J's IeeeDivideRule in
        # the post-translate pass, so our pre-translator never sees it.
        sql = "SELECT IEEE_DIVIDE(a, b) FROM t"
        rewritten = rewrite_division_by_zero(sql)
        assert "CASE WHEN" not in rewritten


class TestSafeFunctionPrefixAbsorbsRaise:
    """``SAFE.X(...)`` (a TRY shell after safe_helpers) absorbs our raise."""

    def test_safe_ln_negative_still_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``SAFE.LN(-1)`` rewrites to ``TRY(LN(-1))``; the LN raise is
        # caught by TRY → NULL. There is no Div in this expression so
        # our rewriter does nothing — the test is a regression guard
        # against accidental interference with the SAFE prefix path.
        row = _execute(t, con, "SELECT SAFE.LN(-1) AS r")
        assert row == (None,)


class TestNestedDivision:
    """Nested ``(a / b) / c`` wraps both Divs — children before parents."""

    def test_outer_zero_raises(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(
                t,
                con,
                "WITH t AS (SELECT 10 AS a, 5 AS b, 0 AS c) SELECT (a/b)/c FROM t",
            )

    def test_inner_zero_raises(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(
                t,
                con,
                "WITH t AS (SELECT 10 AS a, 0 AS b, 2 AS c) SELECT (a/b)/c FROM t",
            )

    def test_neither_zero_evaluates(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "WITH t AS (SELECT 10 AS a, 5 AS b, 2 AS c) SELECT (a/b)/c FROM t",
        )
        assert row == (1.0,)

    def test_both_divs_wrapped(self) -> None:
        # The outer's WHEN tests the OUTER divisor; the inner's WHEN
        # tests the INNER divisor. Both must appear after the rewrite.
        rewritten = rewrite_division_by_zero("SELECT (a/b)/c FROM t")
        # Outer condition.
        assert "WHEN c = 0" in rewritten
        # Inner condition.
        assert "WHEN b = 0" in rewritten


class TestInsideWindow:
    """``SUM(a/b) OVER ...`` — the inner Div is wrapped."""

    def test_window_with_zero_divisor_raises(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute_all(
                t,
                con,
                """
                WITH t AS (
                  SELECT 1 AS a, 2 AS b, 1 AS x
                  UNION ALL SELECT 4 AS a, 0 AS b, 2 AS x
                )
                SELECT SUM(a/b) OVER (ORDER BY x) FROM t
                """,
            )

    def test_window_non_zero_works(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        rows = _execute_all(
            t,
            con,
            """
            WITH t AS (
              SELECT 1 AS a, 2 AS b, 1 AS x
              UNION ALL SELECT 4 AS a, 2 AS b, 2 AS x
            )
            SELECT SUM(a/b) OVER (ORDER BY x) FROM t
            """,
        )
        assert rows == [(0.5,), (2.5,)]

    def test_window_div_translation_contains_case(self) -> None:
        rewritten = rewrite_division_by_zero(
            "SELECT SUM(a/b) OVER (ORDER BY x) FROM t",
        )
        assert "CASE WHEN b = 0" in rewritten


class TestInsideAggregate:
    """``SUM(a/b)`` — the inner Div is wrapped before aggregation."""

    def test_aggregate_with_zero_divisor_raises(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute(
                t,
                con,
                """
                WITH t AS (
                  SELECT 1 AS a, 2 AS b
                  UNION ALL SELECT 4 AS a, 0 AS b
                )
                SELECT SUM(a/b) FROM t
                """,
            )

    def test_aggregate_non_zero_works(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(
            t,
            con,
            """
            WITH t AS (
              SELECT 1 AS a, 2 AS b
              UNION ALL SELECT 4 AS a, 2 AS b
            )
            SELECT SUM(a/b) FROM t
            """,
        )
        assert row == (2.5,)


class TestWhereClause:
    """``WHERE a/b > 0`` — the Div inside a predicate is wrapped too."""

    def test_where_with_zero_divisor_raises(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(duckdb.InvalidInputException, match="Division by zero"):
            _execute_all(
                t,
                con,
                "WITH t AS (SELECT 1 AS a, 0 AS b) SELECT a FROM t WHERE a/b > 0",
            )

    def test_where_non_zero_passes(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        rows = _execute_all(
            t,
            con,
            "WITH t AS (SELECT 1 AS a, 2 AS b) SELECT a FROM t WHERE a/b > 0",
        )
        assert rows == [(1,)]


class TestParseTolerance:
    """Unparseable SQL falls back to the input string."""

    def test_unparseable_returns_input(self) -> None:
        sql = "THIS IS / NOT SQL"
        # Returns the input unchanged so the downstream transpile
        # surfaces its own parse error.
        assert rewrite_division_by_zero(sql) == sql
