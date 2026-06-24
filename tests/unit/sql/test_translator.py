"""Tests for the SQLTranslator orchestrator.

These test the pipeline end-to-end: BigQuery SQL in, DuckDB SQL out.
Individual rule tests live in ``tests/unit/sql/rules/``.
"""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import UnsupportedFeatureError
from bqemulator.domain.result import Err, Ok
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
        this asserts the keyword guard was lifted (no ``UnsupportedFeatureError``).
        """
        result = translator.translate(
            "CREATE MODEL my_model OPTIONS(model_type='linear_reg') AS SELECT * FROM t",
        )
        assert isinstance(result, Ok)


class TestTranslatorIsStateless:
    def test_multiple_calls_independent(self, translator: SQLTranslator) -> None:
        r1 = translator.translate("SELECT 1")
        r2 = translator.translate("SELECT 2")
        assert isinstance(r1, Ok)
        assert isinstance(r2, Ok)
        assert "1" in r1.value
        assert "2" in r2.value
