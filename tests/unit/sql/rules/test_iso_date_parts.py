"""Tests for the BigQuery ISO + calendar date-part translation rules.

The rules in :mod:`bqemulator.sql.rules.iso_date_parts` bridge a DuckDB
parser gap (no ``ISOWEEK`` specifier inside ``EXTRACT``), the
type-fidelity gap of ``DATE_TRUNC`` (DuckDB returns ``TIMESTAMP`` where
BigQuery returns the operand's type), and BigQuery's week starts
(``WEEK`` = Sunday, ``WEEK(<WEEKDAY>)``, ``ISOWEEK`` = Monday) that
DuckDB's Monday-only ``DATE_TRUNC('WEEK', x)`` cannot express. Each
rule is exercised against a real DuckDB connection so the BigQuery
wire-format expectations are honoured end-to-end.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.datetime_helpers import DATE_TRUNC_FUNCTION
from bqemulator.sql.rules._base import TranslationRule
from bqemulator.sql.rules.iso_date_parts import DateTruncCalendarUnitRule, DateTruncWeekRule
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit

#: BigQuery week granularities and the DuckDB ``DAYOFWEEK`` index
#: (Sunday = 0 … Saturday = 6) of each one's first day.
WEEK_UNITS: list[tuple[str, int]] = [
    ("WEEK", 0),
    ("ISOWEEK", 1),
    ("WEEK(SUNDAY)", 0),
    ("WEEK(MONDAY)", 1),
    ("WEEK(TUESDAY)", 2),
    ("WEEK(WEDNESDAY)", 3),
    ("WEEK(THURSDAY)", 4),
    ("WEEK(FRIDAY)", 5),
    ("WEEK(SATURDAY)", 6),
]

#: One full week, Sunday 2024-05-12 through Saturday 2024-05-18.
ONE_WEEK = [_dt.date(2024, 5, 12) + _dt.timedelta(days=i) for i in range(7)]


def week_start(day: _dt.date, first_day: int) -> _dt.date:
    """Reference BigQuery week truncation, independent of the translator."""
    dayofweek = (day.weekday() + 1) % 7  # Python Monday=0 → Sunday=0
    return day - _dt.timedelta(days=(dayofweek - first_day) % 7)


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    # Mirror the engine: TIMESTAMPTZ arithmetic runs in UTC.
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


def _query_table(
    t: SQLTranslator,
    con: duckdb.DuckDBPyConnection,
    *,
    columns: dict[str, str],
    rows: list[tuple[Any, ...]],
    bq_sql: str,
    with_schema: bool = True,
) -> tuple[list[tuple[Any, ...]], list[str]]:
    """Run *bq_sql* over ``test-project.bqemu.t`` and return ``(rows, column_types)``.

    *columns* maps column name to DuckDB type — the same spelling the
    catalog schema snapshot hands the translator. The table lives in an
    attached ``test-project`` catalog so the translator's three-part
    table reference resolves without rewriting.
    """
    con.execute("ATTACH IF NOT EXISTS ':memory:' AS \"test-project\"")
    con.execute('CREATE SCHEMA IF NOT EXISTS "test-project".bqemu')
    column_ddl = ", ".join(f"{name} {kind}" for name, kind in columns.items())
    con.execute(f'CREATE OR REPLACE TABLE "test-project".bqemu.t ({column_ddl})')
    placeholders = ", ".join("?" for _ in columns)
    for row in rows:
        con.execute(f'INSERT INTO "test-project".bqemu.t VALUES ({placeholders})', list(row))
    schema = {"t": columns} if with_schema else None
    result = t.translate(bq_sql, schema=schema)
    assert isinstance(result, Ok), result
    relation = con.execute(result.value)
    column_types = [str(column[1]) for column in relation.description]
    return relation.fetchall(), column_types


class TestExtractIsoweek:
    """``EXTRACT(ISOWEEK FROM x)`` → ``EXTRACT(WEEK FROM x)``."""

    def test_rewrites_specifier(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT EXTRACT(ISOWEEK FROM DATE '2024-03-15') AS w")
        assert isinstance(result, Ok)
        assert "ISOWEEK" not in result.value.upper()
        assert "WEEK" in result.value.upper()

    def test_returns_iso_week(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT EXTRACT(ISOWEEK FROM DATE '2024-03-15') AS w")
        assert row == (11,)

    def test_unrelated_extract_untouched(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The rule must NOT alter ``EXTRACT(YEAR FROM ...)`` or other
        # non-ISOWEEK specifiers.
        row = _execute(t, con, "SELECT EXTRACT(YEAR FROM DATE '2024-03-15') AS y")
        assert row == (2024,)


class TestDateTruncIsoyear:
    """``DATE_TRUNC(date, ISOYEAR)`` → ``CAST(DATE_TRUNC('ISOYEAR', date) AS DATE)``."""

    def test_wraps_in_date_cast(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT DATE_TRUNC(DATE '2024-01-02', ISOYEAR) AS d")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "CAST" in upper
        assert "AS DATE" in upper

    def test_returns_date_value(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT DATE_TRUNC(DATE '2024-01-02', ISOYEAR) AS d")
        assert row == (_dt.date(2024, 1, 1),)

    def test_iso_year_starting_in_previous_gregorian_year(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # BigQuery reference example: ISO year 2015 starts Monday 2014-12-29.
        row = _execute(t, con, "SELECT DATE_TRUNC(DATE '2015-06-15', ISOYEAR) AS d")
        assert row == (_dt.date(2014, 12, 29),)

    def test_other_unit_keeps_its_own_boundary(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT DATE_TRUNC(DATE '2024-03-15', MONTH) AS d")
        assert row == (_dt.date(2024, 3, 1),)

    def test_timestamp_operand_not_cast_to_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # TIMESTAMP_TRUNC(..., ISOYEAR) reaches DuckDB as the same
        # ``DATE_TRUNC('ISOYEAR', x)`` call, but must keep its TIMESTAMP type.
        result = t.translate(
            "SELECT TIMESTAMP_TRUNC(TIMESTAMP '2024-01-02 12:00:00 UTC', ISOYEAR) AS d",
        )
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert "TIMESTAMP" in str(desc[0][1]).upper()

    def test_datetime_operand_not_cast_to_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate(
            "SELECT DATETIME_TRUNC(DATETIME '2024-01-02 12:00:00', ISOYEAR) AS d",
        )
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert str(desc[0][1]) == "TIMESTAMP"


class TestDateTruncQuarter:
    """``DATE_TRUNC(date, QUARTER)`` → ``CAST(... AS DATE)``."""

    def test_returns_date(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # Wed 2024-05-15 → start of Q2 = 2024-04-01.
        result = t.translate("SELECT DATE_TRUNC(DATE '2024-05-15', QUARTER) AS d")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert desc[0][1] == "DATE"
        row = con.execute(result.value).fetchone()
        assert row == (_dt.date(2024, 4, 1),)

    def test_passes_through_timestamp_operand(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # When the operand is TIMESTAMP, the rule must NOT cast to DATE.
        result = t.translate(
            "SELECT DATE_TRUNC(TIMESTAMP '2024-05-15 12:00:00 UTC', QUARTER) AS d",
        )
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert "TIMESTAMP" in str(desc[0][1]).upper()


class TestDateTruncWeek:
    """``DATE_TRUNC(x, WEEK | WEEK(<WEEKDAY>) | ISOWEEK)`` honours BigQuery's week start."""

    @pytest.mark.parametrize(("unit", "first_day"), WEEK_UNITS)
    @pytest.mark.parametrize("day", ONE_WEEK, ids=lambda d: d.strftime("%a"))
    def test_date_literal_every_weekday(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        unit: str,
        first_day: int,
        day: _dt.date,
    ) -> None:
        result = t.translate(f"SELECT DATE_TRUNC(DATE '{day.isoformat()}', {unit}) AS d")
        assert isinstance(result, Ok), result
        relation = con.execute(result.value)
        assert relation.description[0][1] == "DATE"
        assert relation.fetchone() == (week_start(day, first_day),)

    def test_bigquery_reference_example(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # BigQuery reference: WEEK(MONDAY) truncates Sunday 2017-11-05 to 2017-10-30.
        row = _execute(t, con, "SELECT DATE_TRUNC(DATE '2017-11-05', WEEK(MONDAY)) AS d")
        assert row == (_dt.date(2017, 10, 30),)

    def test_current_date_operand(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate(
            "SELECT DATE_TRUNC(CURRENT_DATE(), WEEK) AS d, CURRENT_DATE() AS today",
        )
        assert isinstance(result, Ok)
        relation = con.execute(result.value)
        assert relation.description[0][1] == "DATE"
        truncated, today = relation.fetchone()
        assert truncated == week_start(today, 0)

    def test_week_start_independent_of_other_rewrites(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``DATE_ADD`` in the same query makes the datetime pre-translator
        # re-serialise the whole statement; the week start must survive.
        row = _execute(
            t,
            con,
            "SELECT DATE_TRUNC(DATE '2024-05-15', WEEK(SUNDAY)) AS a, "
            "DATE_TRUNC(DATE '2024-05-15', WEEK(MONDAY)) AS b, "
            "DATE_ADD(DATE '2024-05-15', INTERVAL 1 DAY) AS c",
        )
        assert row == (_dt.date(2024, 5, 12), _dt.date(2024, 5, 13), _dt.date(2024, 5, 16))

    def test_nested_truncations(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # MONTH of 2024-05-15 → Wed 2024-05-01 → Sunday-start week 2024-04-28.
        row = _execute(t, con, "SELECT DATE_TRUNC(DATE_TRUNC(DATE '2024-05-15', MONTH), WEEK) AS d")
        assert row == (_dt.date(2024, 4, 28),)

    def test_null_operand(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT DATE_TRUNC(CAST(NULL AS DATE), WEEK(MONDAY)) AS d")
        assert row == (None,)


class TestDateTruncOverTableColumn:
    """``DATE_TRUNC(col, …)`` over schema-typed table columns.

    The translator resolves column types from the catalog ``schema``;
    the result must carry BigQuery's type for each operand type (DATE →
    DATE, DATETIME → DuckDB ``TIMESTAMP``, TIMESTAMP → DuckDB
    ``TIMESTAMPTZ``) and BigQuery's week start for every week unit.
    """

    @pytest.mark.parametrize(("unit", "first_day"), WEEK_UNITS)
    def test_week_units_over_date_column(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        unit: str,
        first_day: int,
    ) -> None:
        rows, types = _query_table(
            t,
            con,
            columns={"placed_on": "DATE"},
            rows=[(day,) for day in ONE_WEEK],
            bq_sql=f"SELECT DATE_TRUNC(placed_on, {unit}) AS w "
            "FROM `test-project.bqemu.t` ORDER BY placed_on",
        )
        assert types == ["DATE"]
        assert rows == [(week_start(day, first_day),) for day in ONE_WEEK]

    @pytest.mark.parametrize(("unit", "first_day"), WEEK_UNITS)
    def test_week_units_over_column_without_schema(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        unit: str,
        first_day: int,
    ) -> None:
        # Without a schema the operand type is unknown: the week start is
        # still BigQuery's, in DuckDB's natural TIMESTAMP result type.
        rows, types = _query_table(
            t,
            con,
            columns={"placed_on": "DATE"},
            rows=[(day,) for day in ONE_WEEK],
            bq_sql=f"SELECT DATE_TRUNC(placed_on, {unit}) AS w "
            "FROM `test-project.bqemu.t` ORDER BY placed_on",
            with_schema=False,
        )
        assert types == ["TIMESTAMP"]
        assert [row[0].date() for row in rows] == [week_start(day, first_day) for day in ONE_WEEK]

    @pytest.mark.parametrize(("unit", "first_day"), WEEK_UNITS)
    @pytest.mark.parametrize(
        ("duckdb_type", "value"),
        [
            ("TIMESTAMP", _dt.datetime(2024, 5, 15, 13, 45, 7)),  # noqa: DTZ001 — DATETIME
            ("TIMESTAMPTZ", _dt.datetime(2024, 5, 15, 13, 45, 7, tzinfo=_dt.UTC)),
        ],
    )
    def test_week_units_keep_datetime_and_timestamp_types(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        unit: str,
        first_day: int,
        duckdb_type: str,
        value: _dt.datetime,
    ) -> None:
        rows, types = _query_table(
            t,
            con,
            columns={"happened_at": duckdb_type},
            rows=[(value,)],
            bq_sql=f"SELECT DATE_TRUNC(happened_at, {unit}) AS w FROM `test-project.bqemu.t`",
        )
        expected_day = week_start(value.date(), first_day)
        assert types == [con.execute(f"SELECT NULL::{duckdb_type}").description[0][1]]
        assert rows == [(_dt.datetime.combine(expected_day, _dt.time(), tzinfo=value.tzinfo),)]

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            ("DAY", _dt.date(2024, 5, 15)),
            ("MONTH", _dt.date(2024, 5, 1)),
            ("QUARTER", _dt.date(2024, 4, 1)),
            ("YEAR", _dt.date(2024, 1, 1)),
            ("ISOYEAR", _dt.date(2024, 1, 1)),
        ],
    )
    def test_calendar_units_over_date_column(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        unit: str,
        expected: _dt.date,
    ) -> None:
        rows, types = _query_table(
            t,
            con,
            columns={"placed_on": "DATE"},
            rows=[(_dt.date(2024, 5, 15),)],
            bq_sql=f"SELECT DATE_TRUNC(placed_on, {unit}) AS d FROM `test-project.bqemu.t`",
        )
        assert (rows, types) == ([(expected,)], ["DATE"])

    def test_timestamp_trunc_over_timestamp_column_not_cast_to_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        _, types = _query_table(
            t,
            con,
            columns={"happened_at": "TIMESTAMP"},
            rows=[(_dt.datetime(2024, 1, 2, 12),)],  # noqa: DTZ001 — DATETIME is naive
            bq_sql="SELECT TIMESTAMP_TRUNC(happened_at, ISOYEAR) AS d FROM `test-project.bqemu.t`",
        )
        assert types == ["TIMESTAMP"]

    @pytest.mark.parametrize("unit", ["DAY", "MONTH", "QUARTER", "YEAR", "ISOYEAR"])
    @pytest.mark.parametrize("function", ["TIMESTAMP_TRUNC", "DATETIME_TRUNC"])
    def test_timestamp_and_datetime_trunc_over_date_are_datetime(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, function: str, unit: str
    ) -> None:
        # A DATE operand coerces to DATETIME — BigQuery's DATE → DATETIME
        # coercion — so neither truncation returns DATE, unlike DATE_TRUNC.
        expected_day = _execute(t, con, f"SELECT DATE_TRUNC(DATE '2024-05-15', {unit}) AS d")
        assert isinstance(expected_day, tuple)
        expected = _dt.datetime.combine(expected_day[0], _dt.time())
        for bq_sql in (
            f"SELECT {function}(placed_on, {unit}) AS d FROM `test-project.bqemu.t`",
            f"SELECT {function}(DATE '2024-05-15', {unit}) AS d",
        ):
            rows, types = _query_table(
                t,
                con,
                columns={"placed_on": "DATE"},
                rows=[(_dt.date(2024, 5, 15),)],
                bq_sql=bq_sql,
            )
            assert (rows, types) == ([(expected,)], ["TIMESTAMP"]), bq_sql

    @pytest.mark.parametrize("unit", ["MONTH", "WEEK", "WEEK(MONDAY)"])
    def test_date_plus_interval_operand_is_datetime(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, unit: str
    ) -> None:
        # BigQuery's ``date + INTERVAL`` operator returns DATETIME, so the
        # truncation must not be cast to DATE — directly or via a CTE.
        for bq_sql in (
            (
                f"SELECT DATE_TRUNC(placed_on + INTERVAL 1 DAY, {unit}) AS d "
                "FROM `test-project.bqemu.t`"
            ),
            (
                "WITH c AS (SELECT placed_on + INTERVAL 1 DAY AS x FROM `test-project.bqemu.t`) "
                f"SELECT DATE_TRUNC(x, {unit}) AS d FROM c"
            ),
        ):
            _, types = _query_table(
                t,
                con,
                columns={"placed_on": "DATE"},
                rows=[(_dt.date(2024, 5, 15),)],
                bq_sql=bq_sql,
            )
            assert types == ["TIMESTAMP"], bq_sql

    def test_date_add_operand_is_date(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The ``DATE_ADD`` function form returns DATE (unlike the operator).
        rows, types = _query_table(
            t,
            con,
            columns={"placed_on": "DATE"},
            rows=[(_dt.date(2024, 5, 15),)],
            bq_sql="SELECT DATE_TRUNC(DATE_ADD(placed_on, INTERVAL 4 DAY), WEEK) AS d "
            "FROM `test-project.bqemu.t`",
        )
        assert (rows, types) == ([(_dt.date(2024, 5, 19),)], ["DATE"])


class TestDateTruncHandoffMatching:
    """Each ``DATE_TRUNC`` rule expands only well-formed handoff calls in its granularity family."""

    @pytest.mark.parametrize("rule", [DateTruncCalendarUnitRule(), DateTruncWeekRule()])
    @pytest.mark.parametrize(
        "call_sql",
        [
            f"{DATE_TRUNC_FUNCTION}(d)",
            f"{DATE_TRUNC_FUNCTION}(d, 'MONTH', 'WEEK(MONDAY)')",
            f"{DATE_TRUNC_FUNCTION}(d, 1)",
            f"{DATE_TRUNC_FUNCTION}(d, x)",
            f"{DATE_TRUNC_FUNCTION}(d, 'HOUR')",
            f"{DATE_TRUNC_FUNCTION}(d, 'WEEK')",
            f"{DATE_TRUNC_FUNCTION}(d, 'WEEK(FUNDAY)')",
            "SOME_OTHER_FUNCTION(d, 'MONTH')",
            "DATE_TRUNC('MONTH', d)",
            "DATE_TRUNC('WEEK', d)",
        ],
    )
    def test_ignores_malformed_or_foreign_calls(self, rule: TranslationRule, call_sql: str) -> None:
        node = sqlglot.parse_one(call_sql, read="duckdb")
        assert not rule.applies_to(node)
        assert rule.rewrite(node) is node

    @pytest.mark.parametrize(
        ("granularity", "matching_rule"),
        [
            ("DAY", DateTruncCalendarUnitRule),
            ("month", DateTruncCalendarUnitRule),
            ("QUARTER", DateTruncCalendarUnitRule),
            ("YEAR", DateTruncCalendarUnitRule),
            ("ISOYEAR", DateTruncCalendarUnitRule),
            ("WEEK(SUNDAY)", DateTruncWeekRule),
            ("week(wednesday)", DateTruncWeekRule),
            ("WEEK(SATURDAY)", DateTruncWeekRule),
        ],
    )
    def test_exactly_one_rule_matches_each_granularity(
        self, granularity: str, matching_rule: type[TranslationRule]
    ) -> None:
        node = sqlglot.parse_one(f"{DATE_TRUNC_FUNCTION}(d, '{granularity}')", read="duckdb")
        assert isinstance(node, exp.Anonymous)
        matches = [
            type(rule)
            for rule in (DateTruncCalendarUnitRule(), DateTruncWeekRule())
            if rule.applies_to(node)
        ]
        assert matches == [matching_rule]
