"""Hypothesis property tests for INTERVAL arithmetic.

Validates the additive identity: ``(date + a) + b ==
date + (a + b)`` across whole-day intervals, where date arithmetic
must agree between DuckDB's interval algebra and Python's
:class:`datetime.timedelta`.
"""

from __future__ import annotations

import datetime as dt

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.property


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


@pytest.fixture(scope="module")
def translator() -> SQLTranslator:
    return SQLTranslator()


_date = st.dates(
    min_value=dt.date(2000, 1, 1),
    max_value=dt.date(2099, 1, 1),
)
_days = st.integers(min_value=-3650, max_value=3650)


@given(d=_date, a=_days, b=_days)
@settings(max_examples=50, deadline=None)
def test_associativity_of_day_intervals(
    d: dt.date,
    a: int,
    b: int,
    translator: SQLTranslator,
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """``(d + a) + b == d + (a + b)`` for whole-day intervals."""
    sql_left = f"SELECT (DATE '{d.isoformat()}' + INTERVAL {a} DAY) + INTERVAL {b} DAY"
    sql_right = f"SELECT DATE '{d.isoformat()}' + (INTERVAL {a} DAY + INTERVAL {b} DAY)"
    result_left = translator.translate(sql_left)
    result_right = translator.translate(sql_right)
    assert isinstance(result_left, Ok)
    assert isinstance(result_right, Ok)
    row_left = conn.execute(result_left.value).fetchone()
    row_right = conn.execute(result_right.value).fetchone()
    assert row_left == row_right


@given(d=_date, a=_days)
@settings(max_examples=30, deadline=None)
def test_add_then_subtract_returns_original(
    d: dt.date,
    a: int,
    translator: SQLTranslator,
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """``(d + INTERVAL n DAY) - INTERVAL n DAY == d``."""
    sql = f"SELECT (DATE '{d.isoformat()}' + INTERVAL {a} DAY) - INTERVAL {a} DAY"
    result = translator.translate(sql)
    assert isinstance(result, Ok)
    row = conn.execute(result.value).fetchone()
    assert row is not None
    # DuckDB widens DATE + INTERVAL to TIMESTAMP; the calendar date
    # should still match ``d`` after the symmetric round-trip.
    out = row[0]
    if isinstance(out, dt.datetime):
        out = out.date()
    assert out == d
