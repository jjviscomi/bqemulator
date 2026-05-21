"""Tests for the Bucket I datetime / format / parse semantic rules.

Every rule in :mod:`bqemulator.sql.rules.datetime_semantics` bridges a
DuckDB ↔ BigQuery output divergence. The tests exercise the rules
end-to-end through :class:`SQLTranslator` + a real DuckDB connection so
the BigQuery wire-format expectations are honoured.
"""

from __future__ import annotations

import datetime as _dt

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    """Translate *sql* and run the result, returning the first row."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestExtractDateFromTimestamp:
    """``EXTRACT(DATE FROM ts)`` → ``CAST(ts AS DATE)``."""

    def test_returns_date(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT EXTRACT(DATE FROM TIMESTAMP '2024-01-15 12:34:56+00') AS d")
        assert row == (_dt.date(2024, 1, 15),)

    def test_date_specifier_handled(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT EXTRACT(DATE FROM TIMESTAMP '2024-01-15 12:00:00+00') AS d")
        assert isinstance(result, Ok)
        # Should not contain ``EXTRACT(DATE FROM`` which DuckDB rejects.
        assert "EXTRACT(DATE FROM" not in result.value.upper()


class TestExtractDayofweek:
    """``EXTRACT(DAYOFWEEK FROM x)`` → 1-indexed (Sun = 1) value."""

    def test_tuesday_returns_3(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 2024-04-23 is a Tuesday; BigQuery returns 3 (Sun=1, Mon=2, Tue=3).
        row = _execute(t, con, "SELECT EXTRACT(DAYOFWEEK FROM DATE '2024-04-23') AS d")
        assert row == (3,)

    def test_sunday_returns_1(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT EXTRACT(DAYOFWEEK FROM DATE '2024-04-21') AS d")
        assert row == (1,)

    def test_saturday_returns_7(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT EXTRACT(DAYOFWEEK FROM DATE '2024-04-27') AS d")
        assert row == (7,)


class TestExtractWeekSundayStart:
    """``EXTRACT(WEEK FROM x)`` → Sunday-start Gregorian week."""

    def test_mid_march_returns_10(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 2024-03-15: Friday, Sunday-start week 10 in BQ.
        row = _execute(t, con, "SELECT EXTRACT(WEEK FROM DATE '2024-03-15') AS w")
        assert row == (10,)

    def test_jan_1_returns_0(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # Days before the first Sunday of the year are week 0.
        row = _execute(t, con, "SELECT EXTRACT(WEEK FROM DATE '2024-01-01') AS w")
        assert row == (0,)

    def test_first_sunday_returns_1(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 2024-01-07 is the first Sunday → week 1.
        row = _execute(t, con, "SELECT EXTRACT(WEEK FROM DATE '2024-01-07') AS w")
        assert row == (1,)

    def test_isoweek_still_iso(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # ``EXTRACT(ISOWEEK FROM ...)`` rewrites to DuckDB's WEEK (ISO),
        # so 2024-03-15 stays at ISO week 11.
        row = _execute(t, con, "SELECT EXTRACT(ISOWEEK FROM DATE '2024-03-15') AS w")
        assert row == (11,)


class TestConcatStringType:
    """``a || b`` → ``CAST(a || b AS VARCHAR)`` preserves column type on NULL."""

    def test_null_concat_returns_string(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate('SELECT CONCAT(CAST(NULL AS STRING), "x") AS result')
        assert isinstance(result, Ok)
        cursor = con.execute(result.value)
        desc = cursor.description
        assert "VARCHAR" in str(desc[0][1]).upper()
        assert cursor.fetchone() == (None,)

    def test_normal_concat_unchanged(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT CONCAT('a', 'b') AS result")
        assert row == ("ab",)


class TestApproxCountDistinctExact:
    """``APPROX_COUNT_DISTINCT(x)`` → ``COUNT(DISTINCT x)``."""

    def test_exact_count(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        sql = (
            "SELECT APPROX_COUNT_DISTINCT(n) AS n FROM "
            "(SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 "
            "UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 "
            "UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 "
            "UNION ALL SELECT 10) t"
        )
        row = _execute(t, con, sql)
        assert row == (10,)


class TestApproxQuantilesDiscrete:
    """``APPROX_QUANTILE(x, [q...])`` → ``quantile_disc(x, [q...])``."""

    def test_quartiles(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        sql = (
            "SELECT APPROX_QUANTILES(n, 4) AS q FROM "
            "(SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 "
            "UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 "
            "UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 "
            "UNION ALL SELECT 10) t"
        )
        row = _execute(t, con, sql)
        assert row == ([1, 3, 5, 8, 10],)


class TestFormatPrintf:
    """``FORMAT(fmt, args)`` → ``printf(fmt, args)`` for printf-style specifiers."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT FORMAT('%05d', 42) AS s", "00042"),
            ("SELECT FORMAT('%s=%d', 'n', 7) AS s", "n=7"),
            ("SELECT FORMAT('%.3f', 3.14159) AS s", "3.142"),
            ("SELECT FORMAT('%x', 255) AS s", "ff"),
            ("SELECT FORMAT('|%-10s|', 'hi') AS s", "|hi        |"),
        ],
    )
    def test_specifiers(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        sql: str,
        expected: str,
    ) -> None:
        row = _execute(t, con, sql)
        assert row == (expected,)


class TestJsonTypeLower:
    """``JSON_TYPE(x)`` → ``LOWER(JSON_TYPE(x))``."""

    def test_object_lower(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_TYPE(PARSE_JSON('{\"a\": 1}')) AS t")
        assert row == ("object",)

    def test_array_lower(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_TYPE(PARSE_JSON('[1, 2, 3]')) AS t")
        assert row == ("array",)


class TestParseTime:
    """``PARSE_TIME(fmt, value)`` → ``CAST(strptime(value, fmt) AS TIME)``."""

    def test_basic_format(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate("SELECT PARSE_TIME('%H:%M:%S', '12:34:56') AS t")
        assert isinstance(result, Ok)
        cursor = con.execute(result.value)
        desc = cursor.description
        assert str(desc[0][1]).upper() == "TIME"
        assert cursor.fetchone() == (_dt.time(12, 34, 56),)


class TestParseTimestampUtc:
    """``PARSE_TIMESTAMP`` → wrapped in ``timezone('UTC', strptime(...))``."""

    def test_returns_timestamptz(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate(
            "SELECT PARSE_TIMESTAMP('%Y-%m-%d %H:%M:%S', '2024-01-15 12:34:56') AS ts",
        )
        assert isinstance(result, Ok)
        cursor = con.execute(result.value)
        desc = cursor.description
        assert "TIME ZONE" in str(desc[0][1]).upper()
        row = cursor.fetchone()
        assert row is not None
        assert row[0].tzinfo is not None


class TestFormatTime:
    """``FORMAT_TIME(fmt, t)`` → ``STRFTIME(DATE '1970-01-01' + t, fmt')``."""

    def test_basic_hms(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT FORMAT_TIME('%H:%M:%S', TIME '12:30:45') AS s")
        assert row == ("12:30:45",)

    def test_fractional_e3s(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT FORMAT_TIME('%H:%M:%E3S', TIME '12:30:45.123') AS s",
        )
        assert row == ("12:30:45.123",)

    def test_strftime_against_time_routed_through_date(self, t: SQLTranslator) -> None:
        """The translated SQL must combine TIME with a DATE prefix."""
        result = t.translate("SELECT FORMAT_TIME('%H:%M:%S', TIME '12:30:45') AS s")
        assert isinstance(result, Ok)
        # ``STRFTIME(CAST(... AS TIME), …)`` would fail at execution; we
        # rewrite to ``STRFTIME(CAST('1970-01-01' AS DATE) + …, …)``.
        sql_upper = result.value.upper()
        assert "1970-01-01" in result.value
        assert "STRFTIME" in sql_upper


class TestParseDatetime:
    """``PARSE_DATETIME(fmt, value)`` → ``strptime(value, fmt)``."""

    def test_basic_format(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate(
            "SELECT PARSE_DATETIME('%Y-%m-%d %H:%M:%S', '2024-01-15 12:30:45') AS d",
        )
        assert isinstance(result, Ok)
        cursor = con.execute(result.value)
        desc = cursor.description
        # Naive timestamp (no TIME ZONE) lands on BQ wire as DATETIME.
        assert "TIME ZONE" not in str(desc[0][1]).upper()
        assert cursor.fetchone() == (_dt.datetime(2024, 1, 15, 12, 30, 45),)  # noqa: DTZ001 — DATETIME is naive in BQ

    def test_iso_format(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT PARSE_DATETIME('%Y-%m-%dT%H:%M:%S', '2024-01-15T12:30:45') AS d",
        )
        assert row == (_dt.datetime(2024, 1, 15, 12, 30, 45),)  # noqa: DTZ001 — DATETIME is naive in BQ


class TestTimeFromTimestamptz:
    """``TIME(timestamp)`` → ``CAST(timezone('UTC', ts) AS TIME)``."""

    def test_preserves_utc(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT TIME(TIMESTAMP '2024-01-15 12:30:45 UTC') AS t",
        )
        assert row == (_dt.time(12, 30, 45),)

    def test_bare_time_literal_unchanged(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """``TIME '12:30:45'`` literals must not trigger the timezone wrap."""
        row = _execute(t, con, "SELECT TIME '12:30:45' AS t")
        assert row == (_dt.time(12, 30, 45),)


class TestTimeTrunc:
    """``TIME_TRUNC(time, unit)`` → ``CAST(DATE_TRUNC(unit, DATE + time) AS TIME)``."""

    def test_truncate_hour(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT TIME_TRUNC(TIME '12:30:45', HOUR) AS t",
        )
        assert row == (_dt.time(12, 0, 0),)

    def test_truncate_minute(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT TIME_TRUNC(TIME '12:30:45', MINUTE) AS t",
        )
        assert row == (_dt.time(12, 30, 0),)

    def test_result_is_time_typed(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate("SELECT TIME_TRUNC(TIME '12:30:45', HOUR) AS t")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert str(desc[0][1]).upper() == "TIME"


class TestAtTimeZoneNumericOffset:
    """``ts AT TIME ZONE '+HH:MM'`` → UTC-naive + signed HOUR/MINUTE intervals.

    DuckDB accepts named zones but not the BigQuery-flavoured ``+HH:MM``
    literal — the rule rewrites the offset into algebraically-equivalent
    HOUR + MINUTE intervals so the output matches real BigQuery.
    """

    def test_positive_offset_adds(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
    ) -> None:
        # 16:00 UTC + 02:30 = 18:30 local (no DST under numeric offset).
        row = _execute(
            t,
            con,
            "SELECT TIMESTAMP '2026-05-21 16:00:00+00' AT TIME ZONE '+02:30' AS local_ts",
        )
        assert row == (_dt.datetime(2026, 5, 21, 18, 30, 0),)  # noqa: DTZ001

    def test_negative_offset_subtracts(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
    ) -> None:
        # 16:00 UTC - 04:30 = 11:30 local.
        row = _execute(
            t,
            con,
            "SELECT TIMESTAMP '2026-05-21 16:00:00+00' AT TIME ZONE '-04:30' AS local_ts",
        )
        assert row == (_dt.datetime(2026, 5, 21, 11, 30, 0),)  # noqa: DTZ001

    def test_zero_offset_is_identity(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _execute(
            t,
            con,
            "SELECT TIMESTAMP '2026-05-21 16:00:00+00' AT TIME ZONE '+00:00' AS local_ts",
        )
        assert row == (_dt.datetime(2026, 5, 21, 16, 0, 0),)  # noqa: DTZ001
