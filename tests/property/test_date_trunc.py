"""Hypothesis property tests for ``DATE_TRUNC`` over every date granularity.

For any date, any BigQuery date granularity (``DAY``, ``WEEK``,
``WEEK(<WEEKDAY>)``, ``ISOWEEK``, ``MONTH``, ``QUARTER``, ``YEAR``,
``ISOYEAR``), and any operand type (DATE literal, DATE / DATETIME /
TIMESTAMP column), the translated query must return the start of the
BigQuery period containing the input — computed here independently with
:mod:`datetime` — in the operand's BigQuery type.
"""

from __future__ import annotations

from collections.abc import Callable
import datetime as dt

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.property

_WEEKDAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")


def _week_start(first_day: int) -> Callable[[dt.date], dt.date]:
    """Reference BigQuery week truncation for weeks starting on *first_day* (Sunday = 0)."""

    def truncate(day: dt.date) -> dt.date:
        dayofweek = (day.weekday() + 1) % 7
        return day - dt.timedelta(days=(dayofweek - first_day) % 7)

    return truncate


#: ``(BigQuery granularity, reference truncation)`` for every date granularity.
_GRANULARITIES: tuple[tuple[str, Callable[[dt.date], dt.date]], ...] = (
    ("DAY", lambda day: day),
    ("WEEK", _week_start(0)),
    ("ISOWEEK", _week_start(1)),
    *((f"WEEK({weekday})", _week_start(index)) for index, weekday in enumerate(_WEEKDAYS)),
    ("MONTH", lambda day: day.replace(day=1)),
    ("QUARTER", lambda day: day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)),
    ("YEAR", lambda day: day.replace(month=1, day=1)),
    ("ISOYEAR", lambda day: dt.date.fromisocalendar(day.isocalendar().year, 1, 1)),
)

#: Column operand kinds: catalog (DuckDB) column type → expected result type.
_COLUMN_RESULT_TYPES = {
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
}


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("ATTACH ':memory:' AS \"test-project\"")
    connection.execute('CREATE SCHEMA "test-project".bqemu')
    return connection


@pytest.fixture(scope="module")
def translator() -> SQLTranslator:
    return SQLTranslator()


# Naive bounds: the strategy yields DATETIME-style values; TIMESTAMP cases attach UTC.
_moment = st.datetimes(
    min_value=dt.datetime(1000, 1, 8),  # noqa: DTZ001
    max_value=dt.datetime(9999, 12, 24),  # noqa: DTZ001
)
_granularity = st.sampled_from(_GRANULARITIES)


@given(moment=_moment, granularity=_granularity)
@settings(max_examples=200, deadline=None)
def test_date_literal_truncates_to_period_start(
    conn: duckdb.DuckDBPyConnection,
    translator: SQLTranslator,
    moment: dt.datetime,
    granularity: tuple[str, Callable[[dt.date], dt.date]],
) -> None:
    unit_sql, reference = granularity
    day = moment.date()
    result = translator.translate(f"SELECT DATE_TRUNC(DATE '{day.isoformat()}', {unit_sql}) AS w")
    assert isinstance(result, Ok), result
    relation = conn.execute(result.value)
    assert relation.description[0][1] == "DATE"
    assert relation.fetchone() == (reference(day),)


@given(
    moment=_moment,
    granularity=_granularity,
    column_type=st.sampled_from(sorted(_COLUMN_RESULT_TYPES)),
)
@settings(max_examples=200, deadline=None)
def test_column_truncates_to_period_start_in_operand_type(
    conn: duckdb.DuckDBPyConnection,
    translator: SQLTranslator,
    moment: dt.datetime,
    granularity: tuple[str, Callable[[dt.date], dt.date]],
    column_type: str,
) -> None:
    unit_sql, reference = granularity
    conn.execute(f'CREATE OR REPLACE TABLE "test-project".bqemu.t (v {column_type})')
    value: dt.date | dt.datetime = moment.date() if column_type == "DATE" else moment
    if column_type == "TIMESTAMPTZ":
        value = moment.replace(tzinfo=dt.UTC)
    conn.execute('INSERT INTO "test-project".bqemu.t VALUES (?)', [value])

    result = translator.translate(
        f"SELECT DATE_TRUNC(v, {unit_sql}) AS w FROM `test-project.bqemu.t`",
        schema={"t": {"v": column_type}},
    )
    assert isinstance(result, Ok), result
    relation = conn.execute(result.value)
    assert relation.description[0][1] == _COLUMN_RESULT_TYPES[column_type]

    expected_day = reference(moment.date())
    (truncated,) = relation.fetchone()
    if column_type == "DATE":
        assert truncated == expected_day
    else:
        assert truncated == dt.datetime.combine(expected_day, dt.time(), tzinfo=value.tzinfo)
