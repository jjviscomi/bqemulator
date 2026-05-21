"""Tests for the INTERVAL translation rules."""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


@pytest.fixture(scope="module")
def translator() -> SQLTranslator:
    return SQLTranslator()


def _run(translator: SQLTranslator, conn: duckdb.DuckDBPyConnection, bq_sql: str) -> object:
    result = translator.translate(bq_sql)
    assert isinstance(result, Ok), f"translate failed: {result}"
    return conn.execute(result.value).fetchone()


class TestCompoundLiterals:
    def test_year_to_second(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        # Compound literal must be pre-rewritten — DuckDB can't parse it directly.
        row = _run(translator, conn, "SELECT INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND")
        assert row is not None
        td = row[0]
        # 1y2m3d4h5m6.789s — Python timedelta has no month component, so
        # the months get folded into days using DuckDB's internal
        # storage; the seconds component is preserved exactly.
        assert isinstance(td, dt.timedelta)
        assert td.microseconds == 789000
        # year/month folded → at least 423 days for ``1-2`` (DuckDB
        # uses 30 days / month for timedelta conversion).
        assert td.days >= 423


class TestJustifyHours:
    def test_pulls_hours_into_days(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT JUSTIFY_HOURS(INTERVAL 36 HOUR)")
        assert row is not None
        assert row[0] == dt.timedelta(days=1, hours=12)


class TestJustifyDays:
    def test_pulls_days_into_months(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        # Verify via VARCHAR cast (timedelta has no month component).
        result = translator.translate("SELECT JUSTIFY_DAYS(INTERVAL 40 DAY)::VARCHAR")
        assert isinstance(result, Ok)
        row = conn.execute(result.value).fetchone()
        assert row is not None
        assert row[0] == "1 month 10 days"


class TestJustifyInterval:
    def test_combined(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        result = translator.translate(
            "SELECT JUSTIFY_INTERVAL(INTERVAL 40 DAY + INTERVAL 36 HOUR)::VARCHAR",
        )
        assert isinstance(result, Ok)
        row = conn.execute(result.value).fetchone()
        assert row is not None
        assert row[0] == "1 month 11 days 12:00:00"


class TestIntervalArithmetic:
    def test_date_plus_interval(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT DATE '2024-01-15' + INTERVAL 1 DAY")
        assert row is not None
        # DuckDB returns this as a TIMESTAMP (date + interval widens).
        # Naive timestamps for naive comparisons are intentional here.
        assert row[0] == dt.datetime(2024, 1, 16, 0, 0)  # noqa: DTZ001

    def test_timestamp_minus_interval(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT TIMESTAMP '2024-01-15 12:00:00' - INTERVAL 1 HOUR",
        )
        assert row is not None
        # Returns TIMESTAMPTZ.
        expected = dt.datetime(2024, 1, 15, 11, 0, tzinfo=dt.UTC)
        assert row[0].replace(tzinfo=dt.UTC) == expected

    def test_make_interval(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        result = translator.translate("SELECT MAKE_INTERVAL(1, 2, 3, 4, 5, 6)::VARCHAR")
        assert isinstance(result, Ok)
        row = conn.execute(result.value).fetchone()
        assert row is not None
        assert row[0] == "1 year 2 months 3 days 04:05:06"


class TestExtractFromInterval:
    def test_extract_month(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT EXTRACT(MONTH FROM INTERVAL '1' YEAR)")
        assert row is not None
        # DuckDB folds ``1 year`` into 12 months and extract('month',
        # interval) returns the month-component modulo year.
        assert row[0] == 0
