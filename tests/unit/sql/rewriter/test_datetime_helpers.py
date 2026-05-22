"""Tests for the Bucket I datetime pre-translator.

The rewrites in :mod:`bqemulator.sql.rewriter.datetime_helpers` run
*before* SQLGlot's BQ → DuckDB transpile so they can preserve
information the transpile would otherwise drop (the function-call
form of ``DATE_ADD`` etc., the int → ts direction of
``TIMESTAMP_MICROS`` / ``TIMESTAMP_MILLIS``, and the Saturday-end
semantic of ``LAST_DAY(x, WEEK)``).
"""

from __future__ import annotations

import datetime as _dt

import duckdb
import pytest
from sqlglot import exp

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.datetime_helpers import (
    _literal_int,
    _name,
    rewrite_datetime_helpers,
)
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    """Translate *sql* and return the first row."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


def _column_type(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> str:
    """Return the DuckDB column type for the first column of *sql*."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    desc = con.execute(result.value).description
    return str(desc[0][1]).upper()


class TestNoOpSql:
    """``rewrite_datetime_helpers`` is a no-op for SQL without trigger tokens."""

    def test_returns_input_unchanged(self) -> None:
        sql = "SELECT 1, 'x', 3.14"
        assert rewrite_datetime_helpers(sql) is sql

    def test_no_trigger_no_rewrite(self) -> None:
        # ``MONTH`` is not a trigger by itself.
        sql = "SELECT DATE_TRUNC(DATE '2024-01-15', MONTH)"
        assert rewrite_datetime_helpers(sql) is sql


class TestDateAddFunctionCall:
    """``DATE_ADD(date, INTERVAL n DAY)`` keeps DATE return type."""

    def test_function_call_returns_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        col_type = _column_type(t, con, "SELECT DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS d")
        assert col_type == "DATE"
        row = _execute(t, con, "SELECT DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS d")
        assert row == (_dt.date(2024, 1, 22),)

    def test_function_call_sub_returns_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT DATE_SUB(DATE '2024-01-15', INTERVAL 7 DAY) AS d")
        assert row == (_dt.date(2024, 1, 8),)

    def test_operator_form_unwrapped(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The literal ``DATE + INTERVAL`` form returns DATETIME in
        # BigQuery — DON'T cast to DATE.
        col_type = _column_type(t, con, "SELECT DATE '2024-01-15' + INTERVAL 7 DAY AS d")
        assert "TIMESTAMP" in col_type or "DATETIME" in col_type
        # The value is the same date, just a different result type.
        row = _execute(t, con, "SELECT DATE '2024-01-15' + INTERVAL 7 DAY AS d")
        # DuckDB returns a datetime object for TIMESTAMP results.
        assert row is not None
        value = row[0]
        if isinstance(value, _dt.datetime):
            value = value.date()
        assert value == _dt.date(2024, 1, 22)

    def test_null_operand_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT DATE_ADD(CAST(NULL AS DATE), INTERVAL 1 DAY) AS d")
        assert row == (None,)


class TestDateFromUnixDate:
    """``DATE_FROM_UNIX_DATE(n)`` returns DATE."""

    def test_returns_date(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        col_type = _column_type(t, con, "SELECT DATE_FROM_UNIX_DATE(19737) AS d")
        assert col_type == "DATE"
        row = _execute(t, con, "SELECT DATE_FROM_UNIX_DATE(19737) AS d")
        assert row == (_dt.date(2024, 1, 15),)


class TestTimestampMicrosMillis:
    """``TIMESTAMP_MICROS`` / ``TIMESTAMP_MILLIS`` → TIMESTAMPTZ."""

    def test_micros_returns_timestamptz(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        col_type = _column_type(t, con, "SELECT TIMESTAMP_MICROS(1705320000000000) AS ts")
        assert "TIME ZONE" in col_type

    def test_millis_returns_timestamptz(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        col_type = _column_type(t, con, "SELECT TIMESTAMP_MILLIS(1705320000000) AS ts")
        assert "TIME ZONE" in col_type

    def test_seconds_unchanged(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # ``TIMESTAMP_SECONDS`` transpiles to DuckDB's ``TO_TIMESTAMP``
        # which already returns TIMESTAMPTZ — the pre-translator should
        # leave the call alone.
        col_type = _column_type(t, con, "SELECT TIMESTAMP_SECONDS(1705320000) AS ts")
        assert "TIME ZONE" in col_type

    def test_unix_millis_inverse_unaffected(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``UNIX_MILLIS(timestamp)`` returns BIGINT — the pre-translator
        # should not wrap it (BigQuery's ``UnixMillis`` AST is a
        # different node than ``UnixToTime``).
        col_type = _column_type(
            t, con, "SELECT UNIX_MILLIS(TIMESTAMP '2024-01-15 12:00:00+00') AS n"
        )
        assert col_type in {"BIGINT", "INTEGER"}


class TestLastDayWeek:
    """``LAST_DAY(x, WEEK)`` returns the Saturday closing the Sunday-start week."""

    def test_thursday_returns_saturday(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # 2024-02-15 is a Thursday; Saturday end = 2024-02-17.
        row = _execute(t, con, "SELECT LAST_DAY(DATE '2024-02-15', WEEK) AS d")
        assert row == (_dt.date(2024, 2, 17),)

    def test_saturday_returns_self(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 2024-02-17 is Saturday → returns same date.
        row = _execute(t, con, "SELECT LAST_DAY(DATE '2024-02-17', WEEK) AS d")
        assert row == (_dt.date(2024, 2, 17),)

    def test_sunday_returns_next_saturday(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # 2024-02-11 is Sunday → Saturday end = 2024-02-17.
        row = _execute(t, con, "SELECT LAST_DAY(DATE '2024-02-11', WEEK) AS d")
        assert row == (_dt.date(2024, 2, 17),)

    def test_last_day_month_unaffected(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``LAST_DAY(x, MONTH)`` was correctly handled by SQLGlot
        # already; the rewrite is scoped to WEEK only.
        row = _execute(t, con, "SELECT LAST_DAY(DATE '2024-02-15', MONTH) AS d")
        assert row == (_dt.date(2024, 2, 29),)


class TestParseFailureTolerance:
    """Parse failures are tolerated — the input is returned unchanged."""

    def test_invalid_sql_with_last_day_week_token_returned_as_is(self) -> None:
        """Garbage SQL with LAST_DAY + WEEK trigger tokens returned unchanged."""
        sql = "LAST_DAY WEEK ((( garbage"
        assert rewrite_datetime_helpers(sql) == sql

    def test_invalid_sql_with_date_add_token_returned_as_is(self) -> None:
        """Garbage SQL with DATE_ADD trigger returned unchanged."""
        sql = "DATE_ADD ))) bad"
        assert rewrite_datetime_helpers(sql) == sql

    def test_invalid_sql_with_timestamp_micros_token_returned_as_is(self) -> None:
        """Garbage SQL with TIMESTAMP_MICROS trigger returned unchanged."""
        sql = "TIMESTAMP_MICROS ((( bad"
        assert rewrite_datetime_helpers(sql) == sql


class TestNoModificationPath:
    """Trigger token present but no matching node — return input unchanged."""

    def test_last_day_week_token_in_string_only(self) -> None:
        """LAST_DAY + WEEK trigger tokens inside string literal don't fire."""
        # Both trigger tokens present in a string literal so the
        # upper() short-circuit fires; the actual LAST_DAY node uses
        # MONTH, so the unit filter inside _rewrite_last_day_week
        # skips and modified remains False.
        sql = "SELECT 'LAST_DAY WEEK' AS lbl, LAST_DAY(DATE '2024-02-15', MONTH) AS d"
        out = rewrite_datetime_helpers(sql)
        # The unit-match check uses the Var.name branch in _name —
        # exercises the non-Literal path.
        assert out == sql

    def test_date_add_trigger_in_string_only(self) -> None:
        """DATE_ADD substring in literal doesn't fire when no actual call exists."""
        sql = "SELECT 'DATE_ADD only in string' AS lbl"
        out = rewrite_datetime_helpers(sql)
        assert out == sql

    def test_timestamp_micros_trigger_in_string_only(self) -> None:
        """TIMESTAMP_MICROS substring in literal doesn't fire."""
        sql = "SELECT 'TIMESTAMP_MICROS only in string' AS lbl"
        out = rewrite_datetime_helpers(sql)
        assert out == sql


class TestAlreadyCastAsDate:
    """A pre-existing ``CAST(DATE_ADD(...) AS DATE)`` is not double-wrapped."""

    def test_hand_written_cast_skipped(self) -> None:
        """Hand-written CAST(DATE_ADD(...) AS DATE) is left as a single cast."""
        sql = "SELECT CAST(DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS DATE) AS d"
        out = rewrite_datetime_helpers(sql)
        # Only one CAST in the output — no double-wrap from the rewriter.
        # (The output is the canonical SQLGlot serialisation; should
        #  still have exactly one CAST.)
        assert out.upper().count("CAST(") == 1

    def test_non_date_cast_still_inner_wrapped(self) -> None:
        """CAST(DATE_ADD(...) AS STRING) gets its inner DATE_ADD re-wrapped."""
        sql = "SELECT CAST(DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS STRING) AS d"
        out = rewrite_datetime_helpers(sql)
        # The non-DATE cast doesn't satisfy the skip; rewriter wraps
        # the inner DATE_ADD with an additional CAST AS DATE.
        upper = out.upper()
        # The output should contain at least one inner CAST AS DATE
        # (the one added by the rewriter, plus the outer STRING cast).
        assert "AS DATE" in upper
        assert "AS STRING" in upper


class TestTimestampMicrosMillisMixed:
    """Mixed ``TIMESTAMP_MICROS`` + ``TIMESTAMP_SECONDS`` triggers continue on the latter."""

    def test_mixed_micros_and_seconds_only_micros_rewritten(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """When both are present, MICROS gets rewritten while SECONDS skips (scale=None)."""
        # TIMESTAMP_MICROS triggers the rewriter pass; TIMESTAMP_SECONDS
        # produces a UnixToTime node with scale=None which falls to
        # the line-144 continue branch.
        sql = "SELECT TIMESTAMP_MICROS(1705320000000000) AS m, TIMESTAMP_SECONDS(1705320000) AS s"
        result = t.translate(sql)
        assert isinstance(result, Ok)
        # Both columns should land as TIMESTAMPTZ — the MICROS one via
        # our rewrite, the SECONDS one via DuckDB's native TO_TIMESTAMP.
        desc = con.execute(result.value).description
        assert "TIME ZONE" in str(desc[0][1]).upper()
        assert "TIME ZONE" in str(desc[1][1]).upper()


class TestLiteralIntHelper:
    """Direct coverage of the private ``_literal_int`` helper.

    The helper extracts an integer from a ``Literal`` AST node and
    tolerates non-numeric strings. Some shapes aren't reachable
    through the SQLGlot-emitted AST under normal SQL, so the helper
    is exercised directly.
    """

    def test_none_returns_none(self) -> None:
        """A None input returns None (early short-circuit)."""
        assert _literal_int(None) is None

    def test_non_literal_returns_none(self) -> None:
        """A non-Literal node returns None (isinstance guard)."""
        assert _literal_int(exp.Var(this="X")) is None

    def test_int_literal_returns_value(self) -> None:
        """A numeric literal returns its int value."""
        assert _literal_int(exp.Literal.number(7)) == 7

    def test_string_literal_with_int_text_returns_value(self) -> None:
        """A string-typed Literal whose text parses returns the int value."""
        assert _literal_int(exp.Literal.string("42")) == 42

    def test_string_literal_with_non_numeric_returns_none(self) -> None:
        """A string-typed Literal whose text isn't numeric returns None."""
        assert _literal_int(exp.Literal.string("not_a_number")) is None

    def test_non_string_non_numeric_returns_none(self) -> None:
        """A Literal whose ``.this`` doesn't parse as int returns None."""
        # Build a Literal with non-numeric string content on the
        # non-string branch by passing is_string=False directly.
        node = exp.Literal(this="abc", is_string=False)
        assert _literal_int(node) is None


class TestNameHelper:
    """Direct coverage of the private ``_name`` helper."""

    def test_literal_returns_text(self) -> None:
        """A Literal node returns ``str(node.this)``."""
        assert _name(exp.Literal.string("WEEK")) == "WEEK"

    def test_var_returns_name(self) -> None:
        """A Var node falls through to the ``node.name`` branch."""
        assert _name(exp.Var(this="WEEK")) == "WEEK"
