"""Tests for the Bucket I datetime BigQuery-AST rewrites.

The rewrites in :mod:`bqemulator.sql.rewriter.datetime_helpers` run on
each parsed BigQuery statement *before* DuckDB generation so they can
preserve information the transpile would otherwise drop (the
function-call form of ``DATE_ADD`` etc., the int → ts direction of
``TIMESTAMP_MICROS`` / ``TIMESTAMP_MILLIS``, the Saturday-end semantic
of ``LAST_DAY(x, WEEK)``, and the function identity and week start of
``DATE_TRUNC(x, <date granularity>)``).
"""

from __future__ import annotations

import datetime as _dt

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.datetime_helpers import (
    DATE_TRUNC_CALENDAR_UNITS,
    DATE_TRUNC_FUNCTION,
    DATE_TRUNC_WEEK_START,
    _date_trunc_granularity,
    _interval_value,
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


def _rewrite(bq_sql: str) -> exp.Expression:
    """Parse *bq_sql* as BigQuery and apply the datetime rewrites."""
    return rewrite_datetime_helpers(sqlglot.parse_one(bq_sql, read="bigquery"))


def _column_type(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> str:
    """Return the DuckDB column type for the first column of *sql*."""
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    desc = con.execute(result.value).description
    return str(desc[0][1]).upper()


class TestNoOpSql:
    """Statements without a rewrite target come back unchanged."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1, 'x', 3.14",
            "SELECT DATE_TRUNC(ts, HOUR) FROM t",
            "SELECT LAST_DAY(DATE '2024-02-15', MONTH)",
            "SELECT 'DATE_TRUNC LAST_DAY WEEK DATE_ADD TIMESTAMP_MICROS' AS lbl",
        ],
    )
    def test_returns_same_unchanged_tree(self, sql: str) -> None:
        tree = sqlglot.parse_one(sql, read="bigquery")
        before = tree.sql(dialect="bigquery")
        assert rewrite_datetime_helpers(tree) is tree
        assert tree.sql(dialect="bigquery") == before


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

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            # Interval values beyond the 32-bit range, which DuckDB only
            # parses in the quoted ``INTERVAL '<n>' <unit>`` form.
            (
                "SELECT TIMESTAMP_MICROS(1705320000000000) AS ts",
                _dt.datetime(2024, 1, 15, 12, tzinfo=_dt.UTC),
            ),
            (
                "SELECT TIMESTAMP_MILLIS(2147483648) AS ts",
                _dt.datetime(1970, 1, 25, 20, 31, 23, 648000, tzinfo=_dt.UTC),
            ),
            (
                "SELECT TIMESTAMP_MICROS(-62135596800000000) AS ts",
                _dt.datetime(1, 1, 1, tzinfo=_dt.UTC),
            ),
            ("SELECT TIMESTAMP_MICROS(1 + 2) AS ts", _dt.datetime(1970, 1, 1, 0, 0, 0, 3, _dt.UTC)),
        ],
    )
    def test_wide_and_computed_operands_execute(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str, expected: _dt.datetime
    ) -> None:
        con.execute("SET TimeZone = 'UTC'")
        assert _execute(t, con, sql) == (expected,)

    def test_interval_value_helper(self) -> None:
        assert _interval_value(exp.Literal.number(1705320000000000)) == exp.Literal.string(
            "1705320000000000",
        )
        negative = sqlglot.parse_one("-5", read="bigquery")
        assert _interval_value(negative) == exp.Literal.string("-5")
        column = exp.column("n")
        copied = _interval_value(column)
        assert copied == column
        assert copied is not column

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


def _handoff_calls(bq_sql: str) -> list[exp.Anonymous]:
    """Return the ``BQEMU_DATE_TRUNC`` calls after rewriting *bq_sql*, outermost first."""
    return [
        node
        for node in _rewrite(bq_sql).find_all(exp.Anonymous)
        if node.name.upper() == DATE_TRUNC_FUNCTION
    ]


def _handoff_args(call: exp.Anonymous) -> list[str]:
    """Return a handoff call's arguments as BigQuery SQL."""
    return [arg.sql(dialect="bigquery") for arg in call.expressions]


class TestDateTrunc:
    """Date-granularity ``DATE_TRUNC`` → ``BQEMU_DATE_TRUNC(x, '<granularity>')``."""

    @pytest.mark.parametrize(
        ("unit", "granularity"),
        [
            ("DAY", "DAY"),
            ("month", "MONTH"),
            ("QUARTER", "QUARTER"),
            ("YEAR", "YEAR"),
            ("ISOYEAR", "ISOYEAR"),
            ("WEEK", "WEEK(SUNDAY)"),
            ("week", "WEEK(SUNDAY)"),
            ("ISOWEEK", "WEEK(MONDAY)"),
            ("WEEK(SUNDAY)", "WEEK(SUNDAY)"),
            ("WEEK(monday)", "WEEK(MONDAY)"),
            ("WEEK(TUESDAY)", "WEEK(TUESDAY)"),
            ("WEEK(Wednesday)", "WEEK(WEDNESDAY)"),
            ("WEEK(THURSDAY)", "WEEK(THURSDAY)"),
            ("WEEK(FRIDAY)", "WEEK(FRIDAY)"),
            ("WEEK(SATURDAY)", "WEEK(SATURDAY)"),
        ],
    )
    def test_emits_handoff_with_canonical_granularity(self, unit: str, granularity: str) -> None:
        (call,) = _handoff_calls(f"SELECT DATE_TRUNC(d, {unit}) AS w FROM t")
        assert _handoff_args(call) == ["d", f"'{granularity}'"]

    def test_every_canonical_granularity_is_in_the_handoff_vocabulary(self) -> None:
        assert {"DAY", "MONTH", "QUARTER", "YEAR", "ISOYEAR"} == DATE_TRUNC_CALENDAR_UNITS
        assert DATE_TRUNC_WEEK_START == {
            "WEEK(SUNDAY)": 0,
            "WEEK(MONDAY)": 1,
            "WEEK(TUESDAY)": 2,
            "WEEK(WEDNESDAY)": 3,
            "WEEK(THURSDAY)": 4,
            "WEEK(FRIDAY)": 5,
            "WEEK(SATURDAY)": 6,
        }

    def test_handoff_is_generated_verbatim_for_duckdb(self) -> None:
        # The week start must reach the post-translator rule intact even
        # though SQLGlot's DuckDB output collapses every week to ``'WEEK'``.
        tree = _rewrite("SELECT DATE_TRUNC(d, WEEK(WEDNESDAY)) FROM t")
        assert f"{DATE_TRUNC_FUNCTION}(d, 'WEEK(WEDNESDAY)')" in tree.sql(dialect="duckdb")

    @pytest.mark.parametrize("unit", ["HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND"])
    def test_time_granularities_untouched(self, unit: str) -> None:
        assert _handoff_calls(f"SELECT DATE_TRUNC(ts, {unit}) FROM t") == []

    def test_time_zone_form_untouched(self) -> None:
        assert _handoff_calls("SELECT DATE_TRUNC(ts, WEEK, 'America/New_York') FROM t") == []

    def test_timestamp_and_datetime_trunc_untouched(self) -> None:
        # Only ``DATE_TRUNC`` returns DATE for a DATE operand; the other
        # truncations reach DuckDB as plain ``DATE_TRUNC`` calls.
        calls = _handoff_calls(
            "SELECT TIMESTAMP_TRUNC(ts, MONTH) AS a, DATETIME_TRUNC(dt, ISOWEEK) AS b, "
            "DATE_TRUNC(d, MONTH) AS c FROM t",
        )
        assert [_handoff_args(call) for call in calls] == [["d", "'MONTH'"]]

    def test_unknown_weekday_left_for_parser(self) -> None:
        assert _handoff_calls("SELECT DATE_TRUNC(d, WEEK(FUNDAY)) FROM t") == []

    def test_nested_calls_both_rewritten(self) -> None:
        outer, inner = _handoff_calls("SELECT DATE_TRUNC(DATE_TRUNC(d, WEEK), ISOWEEK) FROM t")
        assert inner.parent is outer
        assert _handoff_args(outer)[1] == "'WEEK(MONDAY)'"
        assert _handoff_args(inner) == ["d", "'WEEK(SUNDAY)'"]

    def test_granularity_helper(self) -> None:
        assert _date_trunc_granularity(None) is None
        assert _date_trunc_granularity(exp.Var(this="hour")) is None
        assert _date_trunc_granularity(exp.Var(this="quarter")) == "QUARTER"
        assert _date_trunc_granularity(exp.WeekStart(this=exp.Var(this="saturday"))) == (
            "WEEK(SATURDAY)"
        )


class TestAlreadyCastAsDate:
    """A pre-existing ``CAST(DATE_ADD(...) AS DATE)`` is not double-wrapped."""

    def test_hand_written_cast_skipped(self) -> None:
        """Hand-written CAST(DATE_ADD(...) AS DATE) is left as a single cast."""
        tree = _rewrite("SELECT CAST(DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS DATE) AS d")
        date_add = tree.find(exp.DateAdd)
        assert date_add is not None
        assert isinstance(date_add.parent, exp.Cast)
        assert not isinstance(date_add.parent.parent, exp.Cast)

    def test_non_date_cast_still_inner_wrapped(self) -> None:
        """CAST(DATE_ADD(...) AS STRING) gets its inner DATE_ADD wrapped in CAST AS DATE."""
        tree = _rewrite(
            "SELECT CAST(DATE_ADD(DATE '2024-01-15', INTERVAL 7 DAY) AS STRING) AS d",
        )
        date_add = tree.find(exp.DateAdd)
        assert date_add is not None
        inner, outer = date_add.parent, date_add.parent.parent
        assert isinstance(inner, exp.Cast)
        assert inner.to.is_type(exp.DataType.Type.DATE)
        assert isinstance(outer, exp.Cast)
        assert outer.to.is_type(exp.DataType.Type.TEXT)


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
