"""End-to-end tests: BigQuery SQL → SQLTranslator → DuckDB execution.

For each Phase 1 function, we feed a BigQuery SQL query through the
translator and execute the result on a real DuckDB instance, verifying
the output matches expectations. This proves the full pipeline works.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'")
    yield c
    c.close()


def _translate_and_run(
    t: SQLTranslator,
    conn: duckdb.DuckDBPyConnection,
    bq_sql: str,
) -> tuple[object, ...] | None:
    """Translate BigQuery SQL, execute on DuckDB, return first row."""
    result = t.translate(bq_sql)
    assert isinstance(result, Ok), f"Translation failed: {result}"
    return conn.execute(result.value).fetchone()


# ---------------------------------------------------------------------------
# Date / time functions
# ---------------------------------------------------------------------------


class TestDateTimeFunctions:
    def test_date_add(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT DATE_ADD(DATE '2026-04-15', INTERVAL 10 DAY)")
        assert row is not None
        # DuckDB promotes DATE+INTERVAL to TIMESTAMP; check the date part.
        assert str(row[0]).startswith("2026-04-25")

    def test_date_sub(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT DATE_SUB(DATE '2026-04-15', INTERVAL 5 DAY)")
        assert row is not None
        assert str(row[0]).startswith("2026-04-10")

    def test_date_diff(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT DATE_DIFF(DATE '2026-04-15', DATE '2026-04-10', DAY)",
        )
        assert row is not None
        assert row[0] == 5

    def test_extract_year(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT EXTRACT(YEAR FROM DATE '2026-04-15')")
        assert row is not None
        assert row[0] == 2026

    def test_extract_month(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT EXTRACT(MONTH FROM DATE '2026-04-15')")
        assert row is not None
        assert row[0] == 4


# ---------------------------------------------------------------------------
# String functions
# ---------------------------------------------------------------------------


class TestStringFunctions:
    def test_concat(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT CONCAT('hello', ' ', 'world')")
        assert row is not None
        assert row[0] == "hello world"

    def test_regexp_contains(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT REGEXP_CONTAINS('abc123', '[0-9]+')")
        assert row is not None
        assert row[0] is True

    def test_regexp_extract(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, r"SELECT REGEXP_EXTRACT('abc123def', '([0-9]+)')")
        assert row is not None
        assert row[0] == "123"

    def test_regexp_replace(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT REGEXP_REPLACE('hello world', 'world', 'bq')")
        assert row is not None
        assert row[0] == "hello bq"


# ---------------------------------------------------------------------------
# Aggregate functions
# ---------------------------------------------------------------------------


class TestAggregateFunctions:
    def test_count(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT COUNT(*) FROM (SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) sub",
        )
        assert row is not None
        assert row[0] == 3

    def test_sum(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT SUM(x) FROM (VALUES (10), (20), (30)) AS t(x)",
        )
        assert row is not None
        assert row[0] == 60

    def test_avg(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT AVG(x) FROM (VALUES (10.0), (20.0), (30.0)) AS t(x)",
        )
        assert row is not None
        assert abs(row[0] - 20.0) < 0.01

    def test_string_agg(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT STRING_AGG(s, ',') FROM (VALUES ('a'), ('b'), ('c')) AS t(s)",
        )
        assert row is not None
        assert set(row[0].split(",")) == {"a", "b", "c"}

    def test_any_value(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT ANY_VALUE(x) FROM (VALUES (42), (42)) AS t(x)",
        )
        assert row is not None
        assert row[0] == 42

    def test_approx_count_distinct(
        self,
        t: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT APPROX_COUNT_DISTINCT(x) FROM (VALUES (1), (2), (1), (3)) AS t(x)",
        )
        assert row is not None
        # Approximate — should be close to 3.
        assert 2 <= row[0] <= 4


# ---------------------------------------------------------------------------
# Array / struct / JSON functions
# ---------------------------------------------------------------------------


class TestArrayStructJson:
    def test_array_agg(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            "SELECT ARRAY_AGG(x) FROM (VALUES (1), (2), (3)) AS t(x)",
        )
        assert row is not None
        assert sorted(row[0]) == [1, 2, 3]

    def test_array_length(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT ARRAY_LENGTH([10, 20, 30])")
        assert row is not None
        assert row[0] == 3

    def test_unnest(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        result = t.translate("SELECT * FROM UNNEST([1, 2, 3]) AS x")
        assert isinstance(result, Ok)
        rows = conn.execute(result.value).fetchall()
        assert {r[0] for r in rows} == {1, 2, 3}

    def test_struct_literal(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT STRUCT(1 AS x, 'hi' AS y)")
        assert row is not None
        assert row[0]["x"] == 1
        assert row[0]["y"] == "hi"

    def test_json_extract(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(
            t,
            conn,
            """SELECT JSON_EXTRACT('{"key": "value"}', '$.key')""",
        )
        assert row is not None
        # DuckDB returns a JSON value; string representation contains "value".
        assert "value" in str(row[0])


# ---------------------------------------------------------------------------
# Conditional / cast functions
# ---------------------------------------------------------------------------


class TestConditionalFunctions:
    def test_if_function(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT IF(1 > 0, 'yes', 'no')")
        assert row is not None
        assert row[0] == "yes"

    def test_ifnull(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT IFNULL(NULL, 42)")
        assert row is not None
        assert row[0] == 42

    def test_coalesce(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT COALESCE(NULL, NULL, 7)")
        assert row is not None
        assert row[0] == 7

    def test_safe_cast_invalid(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT SAFE_CAST('not_a_number' AS INT64)")
        assert row is not None
        assert row[0] is None

    def test_safe_cast_valid(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        row = _translate_and_run(t, conn, "SELECT SAFE_CAST('123' AS INT64)")
        assert row is not None
        assert row[0] == 123


# ---------------------------------------------------------------------------
# Window functions
# ---------------------------------------------------------------------------


class TestWindowFunctions:
    def test_row_number(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        result = t.translate(
            "SELECT x, ROW_NUMBER() OVER (ORDER BY x) AS rn FROM (VALUES (30), (10), (20)) AS t(x)",
        )
        assert isinstance(result, Ok)
        rows = conn.execute(result.value).fetchall()
        assert len(rows) == 3
        # Verify row numbers are 1, 2, 3.
        rn_values = sorted(r[1] for r in rows)
        assert rn_values == [1, 2, 3]

    def test_sum_over(self, t: SQLTranslator, conn: duckdb.DuckDBPyConnection) -> None:
        result = t.translate(
            "SELECT x, SUM(x) OVER () AS total FROM (VALUES (10), (20), (30)) AS t(x)",
        )
        assert isinstance(result, Ok)
        rows = conn.execute(result.value).fetchall()
        assert all(r[1] == 60 for r in rows)
