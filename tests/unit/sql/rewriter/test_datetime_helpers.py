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

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.datetime_helpers import rewrite_datetime_helpers
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
