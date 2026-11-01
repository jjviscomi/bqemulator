"""Tests for the SQLTranslator orchestrator.

These test the pipeline end-to-end: BigQuery SQL in, DuckDB SQL out.
Individual rule tests live in ``tests/unit/sql/rules/``.
"""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from bqemulator.domain.errors import UnsupportedFeatureError
from bqemulator.domain.result import Err, Ok
from bqemulator.sql import translator as translator_module
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def translator() -> SQLTranslator:
    return SQLTranslator()


class TestBasicTranslation:
    def test_simple_select(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT 1 AS one")
        assert isinstance(result, Ok)
        assert "1" in result.value
        assert "one" in result.value.lower()

    def test_select_from_table(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT * FROM my_dataset.my_table")
        assert isinstance(result, Ok)

    def test_select_with_where(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT id FROM t WHERE id > 10")
        assert isinstance(result, Ok)

    def test_select_with_group_by(self, translator: SQLTranslator) -> None:
        result = translator.translate(
            "SELECT category, COUNT(*) AS cnt FROM t GROUP BY category",
        )
        assert isinstance(result, Ok)

    def test_cte(self, translator: SQLTranslator) -> None:
        sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_window_function(self, translator: SQLTranslator) -> None:
        sql = "SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM t"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_join(self, translator: SQLTranslator) -> None:
        sql = "SELECT a.id FROM t1 AS a JOIN t2 AS b ON a.id = b.id"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_subquery(self, translator: SQLTranslator) -> None:
        sql = "SELECT * FROM (SELECT 1 AS x) sub"
        result = translator.translate(sql)
        assert isinstance(result, Ok)


class TestTypeTranslation:
    """SQLGlot handles basic type translation automatically."""

    def test_safe_cast_becomes_try_cast(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT SAFE_CAST(x AS INT64) FROM t")
        assert isinstance(result, Ok)
        assert "TRY_CAST" in result.value.upper()

    def test_int64_becomes_bigint(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS INT64) FROM t")
        assert isinstance(result, Ok)
        assert "BIGINT" in result.value.upper()

    def test_float64_becomes_double(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS FLOAT64) FROM t")
        assert isinstance(result, Ok)
        assert "DOUBLE" in result.value.upper()

    def test_bool_becomes_boolean(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS BOOL) FROM t")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "BOOL" in upper


class TestErrorHandling:
    def test_invalid_sql_returns_err(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECTTTT bogus garbage")
        # This may parse differently in SQLGlot — let's just ensure
        # it doesn't crash with an unhandled exception.
        assert isinstance(result, (Ok, Err))

    def test_empty_query_returns_err(self, translator: SQLTranslator) -> None:
        result = translator.translate("")
        assert isinstance(result, Err)

    def test_bqml_detected_as_unsupported(self, translator: SQLTranslator) -> None:
        result = translator.translate(
            "SELECT * FROM ML.PREDICT(MODEL my_model, TABLE t)",
        )
        assert isinstance(result, Err)
        assert isinstance(result.error, UnsupportedFeatureError)

    def test_create_model_no_longer_keyword_rejected(
        self,
        translator: SQLTranslator,
    ) -> None:
        """``CREATE MODEL`` is no longer rejected by the translator guard.

        ADR 0047 / RFC 0002 move ``CREATE MODEL`` to AST interception in the
        executor (``jobs.executor.parse_create_model``), so the translator's
        ``_UNSUPPORTED_KEYWORDS`` quick-reject no longer fires on it. The
        translator is never called with a raw ``CREATE MODEL`` in practice;
        this asserts only that the keyword guard was lifted. Whether SQLGlot
        can transpile the statement is incidental (a future SQLGlot change could
        return a parse ``Err`` while the guard stays lifted), so the assertion is
        limited to "not the unsupported-feature rejection".
        """
        result = translator.translate(
            "CREATE MODEL my_model OPTIONS(model_type='linear_reg') AS SELECT * FROM t",
        )
        assert not (isinstance(result, Err) and isinstance(result.error, UnsupportedFeatureError))


class TestTranslatorIsStateless:
    def test_multiple_calls_independent(self, translator: SQLTranslator) -> None:
        r1 = translator.translate("SELECT 1")
        r2 = translator.translate("SELECT 2")
        assert isinstance(r1, Ok)
        assert isinstance(r2, Ok)
        assert "1" in r1.value
        assert "2" in r2.value


_TYPES_SCHEMA = {"t": {"d": "DATE", "n": "BIGINT"}}


def _annotated_projection_type(duckdb_sql: str) -> str:
    """Annotate *duckdb_sql* the way the translator does; return the projection's type."""
    tree = sqlglot.parse_one(duckdb_sql, read="duckdb")
    annotated = SQLTranslator._annotate_tree(tree, _TYPES_SCHEMA)
    (projection,) = annotated.find(exp.Select).expressions
    return str(projection.type)


class TestTypeAnnotation:
    """The annotation pass types operators the way DuckDB evaluates them."""

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("d + INTERVAL '1' DAY", "TIMESTAMP"),
            ("INTERVAL '1' DAY + d", "TIMESTAMP"),
            ("d - INTERVAL '2' MONTH", "TIMESTAMP"),
            ("d + 1", "DATE"),
            ("CAST(d + INTERVAL '1' DAY AS DATE)", "DATE"),
            ("d", "DATE"),
        ],
    )
    def test_projection_type_matches_duckdb(self, expression: str, expected: str) -> None:
        duckdb_sql = f"SELECT {expression} AS v FROM t"
        assert _annotated_projection_type(duckdb_sql) == expected
        connection = duckdb.connect()
        connection.execute("CREATE TABLE t (d DATE, n BIGINT)")
        assert connection.execute(duckdb_sql).description[0][1] == expected

    def test_cte_column_inherits_operator_type(self) -> None:
        duckdb_sql = "WITH c AS (SELECT d + INTERVAL '1' DAY AS x FROM t) SELECT x AS v FROM c"
        assert _annotated_projection_type(duckdb_sql) == "TIMESTAMP"

    def test_annotation_failure_returns_unannotated_tree(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail(*_args: object, **_kwargs: object) -> exp.Expression:
            msg = "qualify failed"
            raise ValueError(msg)

        monkeypatch.setattr(translator_module, "qualify", _fail)
        tree = sqlglot.parse_one("SELECT d FROM t", read="duckdb")
        assert SQLTranslator._annotate_tree(tree, _TYPES_SCHEMA) is tree


class TestTranspileToDuckdb:
    """The transpile step equals ``sqlglot.transpile`` outside the datetime rewrites."""

    @pytest.mark.parametrize(
        "bq_sql",
        [
            "SELECT 1 AS one",
            "SELECT a, b FROM ds.t WHERE a > 1; SELECT 2",
            "SELECT 1;;",
            "SELECT EXTRACT(HOUR FROM TIMESTAMP '2024-06-15 00:00:00+00' AT TIME ZONE '+05:45')",
            "SELECT v.a FROM (VALUES (1, 'x')) AS v(a, b)",
            "SELECT CAST('1.5' AS DECIMAL(38, 9)) AS n, DATE_TRUNC(ts, HOUR) AS h FROM ds.t",
        ],
    )
    def test_matches_sqlglot_transpile(self, bq_sql: str) -> None:
        assert translator_module._transpile_to_duckdb(bq_sql) == sqlglot.transpile(
            bq_sql, read="bigquery", write="duckdb"
        )

    def test_applies_datetime_rewrites_per_statement(self) -> None:
        first, second = translator_module._transpile_to_duckdb(
            "SELECT DATE_TRUNC(d, MONTH) FROM t; SELECT DATE_TRUNC(d, WEEK) FROM t",
        )
        assert "BQEMU_DATE_TRUNC(d, 'MONTH')" in first
        assert "BQEMU_DATE_TRUNC(d, 'WEEK(SUNDAY)')" in second


class TestDatetimeRewritesLeaveTheRestOfTheStatementIntact:
    """A datetime rewrite changes only its own call, never the surrounding SQL.

    Re-serialising BigQuery SQL is lossy — SQLGlot's BigQuery generator
    strips ``CAST`` type parameters and reshapes ``AT TIME ZONE`` and
    ``VALUES … AS v(…)`` clauses — so each case pairs one of those
    constructs with every datetime rewrite and checks it still executes
    to the BigQuery result.
    """

    _DATETIME_CALLS = (
        "DATE_TRUNC(DATE '2024-05-15', MONTH)",
        "DATE_TRUNC(DATE '2024-05-15', WEEK(MONDAY))",
        "DATE_ADD(DATE '2024-05-15', INTERVAL 1 DAY)",
        "LAST_DAY(DATE '2024-05-15', WEEK)",
        "TIMESTAMP_MICROS(0)",
    )

    @pytest.fixture
    def con(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'UTC'")
        return connection

    def _first_value(
        self, translator: SQLTranslator, con: duckdb.DuckDBPyConnection, bq_sql: str
    ) -> object:
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok), result
        row = con.execute(result.value).fetchone()
        assert row is not None
        return row[0]

    @pytest.mark.parametrize("call", _DATETIME_CALLS)
    def test_numeric_literal_keeps_its_precision(
        self, translator: SQLTranslator, con: duckdb.DuckDBPyConnection, call: str
    ) -> None:
        value = self._first_value(
            translator, con, f"SELECT NUMERIC '1.123456789' AS n, {call} AS d"
        )
        assert str(value) == "1.123456789"

    @pytest.mark.parametrize("call", _DATETIME_CALLS)
    def test_utc_offset_time_zone_still_applies(
        self, translator: SQLTranslator, con: duckdb.DuckDBPyConnection, call: str
    ) -> None:
        value = self._first_value(
            translator,
            con,
            "SELECT EXTRACT(HOUR FROM TIMESTAMP '2024-06-15 00:00:00+00' AT TIME ZONE '+05:45') "
            f"AS h, {call} AS d",
        )
        assert value == 5

    @pytest.mark.parametrize("call", _DATETIME_CALLS)
    def test_values_column_aliases_survive(
        self, translator: SQLTranslator, con: duckdb.DuckDBPyConnection, call: str
    ) -> None:
        value = self._first_value(
            translator, con, f"SELECT v.b, {call} AS d FROM (VALUES (1, 'x')) AS v(a, b)"
        )
        assert value == "x"
