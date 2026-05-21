"""Tests for the RANGE<T> translation rules."""

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


class TestRangeConstructor:
    def test_returns_struct_with_start_end(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT RANGE(DATE '2024-01-01', DATE '2024-12-31') AS r")
        assert row is not None
        struct = row[0]
        assert struct["start"] == dt.date(2024, 1, 1)
        assert struct["end"] == dt.date(2024, 12, 31)

    def test_does_not_affect_generate_array(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT GENERATE_ARRAY(1, 5)")
        assert row == ([1, 2, 3, 4, 5],)


class TestRangeContains:
    def test_value_within_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-06-15')",
        )
        assert row == (True,)

    def test_value_at_start_is_inclusive(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-01-01')",
        )
        assert row == (True,)

    def test_value_at_end_is_exclusive(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        # Half-open [start, end) — end itself is NOT contained.
        row = _run(
            translator,
            conn,
            "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-12-31')",
        )
        assert row == (False,)

    def test_value_outside_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2023-12-31')",
        )
        assert row == (False,)


class TestRangeOverlaps:
    def test_overlapping_ranges(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_OVERLAPS("
            "RANGE(DATE '2024-01-01', DATE '2024-06-30'),"
            "RANGE(DATE '2024-04-01', DATE '2024-09-30'))",
        )
        assert row == (True,)

    def test_disjoint_ranges(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_OVERLAPS("
            "RANGE(DATE '2024-01-01', DATE '2024-03-31'),"
            "RANGE(DATE '2024-04-01', DATE '2024-06-30'))",
        )
        assert row == (False,)


class TestRangeIntersect:
    def test_intersection_of_overlapping(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_INTERSECT("
            "RANGE(DATE '2024-01-01', DATE '2024-06-30'),"
            "RANGE(DATE '2024-04-01', DATE '2024-09-30'))",
        )
        assert row is not None
        result = row[0]
        assert result["start"] == dt.date(2024, 4, 1)
        assert result["end"] == dt.date(2024, 6, 30)

    def test_intersection_of_disjoint_returns_null(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_INTERSECT("
            "RANGE(DATE '2024-01-01', DATE '2024-03-31'),"
            "RANGE(DATE '2024-04-01', DATE '2024-06-30'))",
        )
        assert row == (None,)


class TestRangeStart:
    """``RANGE_START(r)`` returns the lower bound of the range."""

    def test_date_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_START(RANGE(DATE '2024-01-01', DATE '2024-12-31')) AS s",
        )
        assert row == (dt.date(2024, 1, 1),)

    def test_datetime_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_START(RANGE(DATETIME '2024-01-01 00:00:00', "
            "DATETIME '2024-12-31 23:59:59')) AS s",
        )
        assert row == (dt.datetime(2024, 1, 1, 0, 0, 0),)  # noqa: DTZ001

    def test_timestamp_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_START(RANGE(TIMESTAMP '2024-01-01 00:00:00 UTC', "
            "TIMESTAMP '2024-12-31 23:59:59 UTC')) AS s",
        )
        assert row is not None
        start = row[0]
        assert isinstance(start, dt.datetime)
        # DuckDB returns the timestamp in the local timezone; normalize
        # to UTC for the comparison.
        assert start.astimezone(dt.UTC).replace(tzinfo=None) == dt.datetime(  # noqa: DTZ001
            2024, 1, 1, 0, 0, 0
        )


class TestRangeEnd:
    """``RANGE_END(r)`` returns the exclusive upper bound of the range."""

    def test_date_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_END(RANGE(DATE '2024-01-01', DATE '2024-12-31')) AS e",
        )
        assert row == (dt.date(2024, 12, 31),)

    def test_datetime_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_END(RANGE(DATETIME '2024-01-01 00:00:00', "
            "DATETIME '2024-12-31 23:59:59')) AS e",
        )
        assert row == (dt.datetime(2024, 12, 31, 23, 59, 59),)  # noqa: DTZ001

    def test_timestamp_range(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT RANGE_END(RANGE(TIMESTAMP '2024-01-01 00:00:00 UTC', "
            "TIMESTAMP '2024-12-31 23:59:59 UTC')) AS e",
        )
        assert row is not None
        end = row[0]
        assert isinstance(end, dt.datetime)
        assert end.astimezone(dt.UTC).replace(tzinfo=None) == dt.datetime(  # noqa: DTZ001
            2024, 12, 31, 23, 59, 59
        )


class TestGenerateRangeArray:
    def test_daily_breakdown_with_constructor_input(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """With ``RANGE(DATE …, DATE …)`` constructor input the rewriter
        sees the STRUCT-of-Cast shape (the pre-translator emits
        ``STRUCT(DATE '…' AS start, DATE '…' AS end)``) and recovers
        DATE; endpoints round-trip as DATE rather than DuckDB's promoted
        TIMESTAMP. ADR 0023 §1.G.
        """
        row = _run(
            translator,
            conn,
            "SELECT GENERATE_RANGE_ARRAY("
            "RANGE(DATE '2024-01-01', DATE '2024-01-04'), INTERVAL 1 DAY)",
        )
        assert row is not None
        arr = row[0]
        assert len(arr) == 3
        assert arr[0] == {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 2)}
        # The end is clipped to the outer range's end (BigQuery semantic).
        assert arr[-1] == {"start": dt.date(2024, 1, 3), "end": dt.date(2024, 1, 4)}

    def test_daily_breakdown_with_literal_input_clips_final_end(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """The trailing sub-range is clipped to the outer range's end.
        ``[2024-01-07, 2024-01-09)`` is *not* the BigQuery behaviour for
        a 2-day step over ``[…, 2024-01-08)`` — BigQuery returns
        ``[2024-01-07, 2024-01-08)``. ADR 0023 §1.G.
        """
        row = _run(
            translator,
            conn,
            "SELECT GENERATE_RANGE_ARRAY(RANGE<DATE> '[2024-01-01, 2024-01-08)', INTERVAL 2 DAY)",
        )
        assert row is not None
        arr = row[0]
        assert len(arr) == 4
        assert arr[0] == {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 3)}
        # The final sub-range is clipped to the outer range's end —
        # 2024-01-08, not 2024-01-09 (start + step).
        assert arr[-1] == {"start": dt.date(2024, 1, 7), "end": dt.date(2024, 1, 8)}


class TestRangeSessionizeRewritten:
    """``RANGE_SESSIONIZE`` is rewritten to a windowed subquery.

    The pre-translator at
    :mod:`bqemulator.sql.rewriter.range_sessionize` replaces the TVF
    call before SQLGlot transpile. The post-translator no longer
    raises ``UnsupportedFeatureError`` — the translation succeeds and
    produces DuckDB SQL with a ``session_range`` STRUCT column.
    """

    def test_translation_succeeds(
        self,
        translator: SQLTranslator,
    ) -> None:
        result = translator.translate(
            "SELECT * FROM RANGE_SESSIONIZE(TABLE my_table, 'col', ['part'])",
        )
        assert isinstance(result, Ok)
        # The rewrite emits the windowed-subquery shape; the
        # `session_range` projection landed correctly.
        assert "session_range" in result.value
        assert "_bqemu_session_id" in result.value
