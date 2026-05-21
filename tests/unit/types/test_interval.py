"""Unit tests for ``types.interval``."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.types.interval import (
    IntervalParts,
    format_bq_interval,
    justify_days_expr,
    justify_hours_expr,
    justify_interval_expr,
    parse_interval_literal,
    parts_to_duckdb_expr,
)

NS_PER_S = 1_000_000_000


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


class TestParseIntervalLiteral:
    @pytest.mark.parametrize(
        ("literal", "span", "expected"),
        [
            ("1-2 3 4:5:6.789", "YEAR TO SECOND", IntervalParts(1, 2, 3, 4, 5, Decimal("6.789"))),
            ("1-2", "YEAR TO MONTH", IntervalParts(years=1, months=2)),
            (
                "3 4:5:6",
                "DAY TO SECOND",
                IntervalParts(days=3, hours=4, minutes=5, seconds=Decimal(6)),
            ),
            ("3 4:5", "DAY TO MINUTE", IntervalParts(days=3, hours=4, minutes=5)),
            ("3 4", "DAY TO HOUR", IntervalParts(days=3, hours=4)),
            ("4:5", "HOUR TO MINUTE", IntervalParts(hours=4, minutes=5)),
            ("4:5:6", "HOUR TO SECOND", IntervalParts(hours=4, minutes=5, seconds=Decimal(6))),
            ("5:6", "MINUTE TO SECOND", IntervalParts(minutes=5, seconds=Decimal(6))),
            # Single units
            ("36", "HOUR", IntervalParts(hours=36)),
            ("1", "YEAR", IntervalParts(years=1)),
            ("90", "SECOND", IntervalParts(seconds=Decimal(90))),
            # Signed
            ("-1-2", "YEAR TO MONTH", IntervalParts(years=-1, months=-2)),
        ],
    )
    def test_parse_canonical_cases(
        self,
        literal: str,
        span: str,
        expected: IntervalParts,
    ) -> None:
        assert parse_interval_literal(literal, span) == expected

    @pytest.mark.parametrize(
        ("literal", "span"),
        [
            ("", "YEAR TO SECOND"),
            ("foo", "HOUR"),
            ("1-2 3 abc", "YEAR TO SECOND"),
            ("1.5", "HOUR"),  # non-integer for an integer-only unit
            ("1-2", "BOGUS SPAN"),
            ("1 2", "DAY TO SECOND"),  # missing H:M:S block where required
        ],
    )
    def test_invalid_inputs_raise(self, literal: str, span: str) -> None:
        with pytest.raises(ValidationError):
            parse_interval_literal(literal, span)


class TestPartsToDuckdbExpr:
    def test_compact_when_all_zero(self) -> None:
        assert parts_to_duckdb_expr(IntervalParts()) == "INTERVAL '0' SECOND"

    def test_omits_zero_components(self) -> None:
        parts = IntervalParts(years=1, hours=4)
        assert parts_to_duckdb_expr(parts) == "(INTERVAL '1' YEAR + INTERVAL '4' HOUR)"

    def test_emits_decimal_seconds(self) -> None:
        parts = IntervalParts(seconds=Decimal("6.789"))
        assert "INTERVAL '6.789' SECOND" in parts_to_duckdb_expr(parts)

    def test_full_round_trip_evaluates(self, conn: duckdb.DuckDBPyConnection) -> None:
        parts = parse_interval_literal("1-2 3 4:5:6", "YEAR TO SECOND")
        expr = parts_to_duckdb_expr(parts)
        result = conn.execute(f"SELECT ({expr})::VARCHAR").fetchone()
        assert result is not None
        assert result[0] == "1 year 2 months 3 days 04:05:06"


class TestJustifyExpressions:
    def test_justify_hours(self, conn: duckdb.DuckDBPyConnection) -> None:
        expr = justify_hours_expr("INTERVAL '36' HOUR")
        row = conn.execute(f"SELECT ({expr})::VARCHAR").fetchone()
        assert row is not None
        assert row[0] == "1 day 12:00:00"

    def test_justify_days(self, conn: duckdb.DuckDBPyConnection) -> None:
        expr = justify_days_expr("INTERVAL '40' DAY")
        row = conn.execute(f"SELECT ({expr})::VARCHAR").fetchone()
        assert row is not None
        assert row[0] == "1 month 10 days"

    def test_justify_interval_combines(self, conn: duckdb.DuckDBPyConnection) -> None:
        expr = justify_interval_expr("INTERVAL '40' DAY + INTERVAL '36' HOUR")
        row = conn.execute(f"SELECT ({expr})::VARCHAR").fetchone()
        assert row is not None
        assert row[0] == "1 month 11 days 12:00:00"

    def test_justify_negative_values(self, conn: duckdb.DuckDBPyConnection) -> None:
        # Verify the formula does not error on negative components.
        expr = justify_hours_expr("INTERVAL '-36' HOUR")
        row = conn.execute(f"SELECT ({expr})::VARCHAR").fetchone()
        assert row is not None
        assert "hour" in row[0].lower() or "day" in row[0].lower()


class TestFormatBqInterval:
    @pytest.mark.parametrize(
        ("months", "days", "nanos", "expected"),
        [
            # ``14 months 3 days 4h:5m:6s`` → folds to ``1-2`` plus the rest.
            (14, 3, 4 * 3600 * NS_PER_S + 5 * 60 * NS_PER_S + 6 * NS_PER_S, "1-2 3 4:5:6"),
            (0, 0, 0, "0-0 0 0:0:0"),
            (24, 0, 0, "2-0 0 0:0:0"),
            # Sign folds onto the leading section.
            (-1, -2, -3 * NS_PER_S, "-0-1 2 0:0:3"),
            # Microsecond precision (DuckDB INTERVAL resolution).
            (0, 0, 1000, "0-0 0 0:0:0.000001"),
            (0, 0, 1_500_000, "0-0 0 0:0:0.0015"),
        ],
    )
    def test_canonical_outputs(
        self,
        months: int,
        days: int,
        nanos: int,
        expected: str,
    ) -> None:
        assert format_bq_interval(months, days, nanos) == expected
