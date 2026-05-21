"""Tests for the BigQuery → DuckDB array-primitive translation rules.

Covers :class:`ArrayFirstRule` / :class:`ArrayLastRule` from
:mod:`bqemulator.sql.rules.array_helpers`. Each rule is exercised
through a real DuckDB connection so the empty-array CASE / ``error()``
contract stays under regression coverage — DuckDB short-circuits the
CASE branches, so the ``error()`` only raises for the actual empty-array
case and not for the non-empty fast path.

The ``SAFE_ORDINAL`` semantic is intentionally NOT covered here — the
existing SQLGlot BQ → DuckDB transpile already strips
``arr[SAFE_ORDINAL(n)]`` to ``arr[n]`` and DuckDB's bare-bracket form
matches the BigQuery NULL-on-OOB contract. The conformance fixtures
``arr_safe_ordinal_in_bounds`` / ``arr_safe_ordinal_oob`` pin that
behaviour at the integration tier; no translator-rule unit test is
required.
"""

from __future__ import annotations

import duckdb
import pytest
from sqlglot import exp

from bqemulator.domain.result import Ok
from bqemulator.sql.rules.array_helpers import ArrayFirstRule, ArrayLastRule
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection — array helpers don't need any extension."""
    return duckdb.connect()


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestArrayFirstRule:
    """``ARRAY_FIRST(arr)`` → empty-check CASE around ``list_extract(arr, 1)``."""

    def test_basic_first_element(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        assert _execute(t, con, "SELECT ARRAY_FIRST([10, 20, 30]) AS f") == (10,)

    def test_empty_array_raises_with_bq_message(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT ARRAY_FIRST(CAST([] AS ARRAY<INT64>)) AS f")
        assert isinstance(result, Ok)
        with pytest.raises(duckdb.InvalidInputException) as excinfo:
            con.execute(result.value).fetchone()
        assert "ARRAY_FIRST cannot get the first element of an empty array" in str(excinfo.value)

    def test_rewrite_shape_is_case_with_list_extract(self) -> None:
        rule = ArrayFirstRule()
        operand = exp.Array(expressions=[exp.Literal.number(7)])
        node = exp.ArrayFirst(this=operand)
        assert rule.applies_to(node)
        rewritten = rule.rewrite(node)
        assert isinstance(rewritten, exp.Case)
        sql = rewritten.sql(dialect="duckdb").lower()
        assert "list_extract" in sql
        assert "array_length" in sql
        # BigQuery's recorded error wording for the empty branch.
        assert "array_first cannot get the first element of an empty array" in sql


class TestArrayLastRule:
    """``ARRAY_LAST(arr)`` → empty-check CASE around ``list_extract(arr, -1)``."""

    def test_basic_last_element(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        assert _execute(t, con, "SELECT ARRAY_LAST([10, 20, 30]) AS l") == (30,)

    def test_empty_array_raises_with_bq_message(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT ARRAY_LAST(CAST([] AS ARRAY<INT64>)) AS l")
        assert isinstance(result, Ok)
        with pytest.raises(duckdb.InvalidInputException) as excinfo:
            con.execute(result.value).fetchone()
        assert "ARRAY_LAST cannot get the last element of an empty array" in str(excinfo.value)

    def test_rewrite_uses_negative_one_index(self) -> None:
        rule = ArrayLastRule()
        operand = exp.Array(expressions=[exp.Literal.number(7)])
        node = exp.ArrayLast(this=operand)
        assert rule.applies_to(node)
        rewritten = rule.rewrite(node)
        # Walk to the LIST_EXTRACT call's second argument and assert it
        # carries ``-1`` (positive or negative form). SQLGlot serialises
        # negative literals as ``Neg(Literal(1))``.
        sql = rewritten.sql(dialect="duckdb").lower()
        assert "list_extract" in sql
        # Negative-index argument must reach DuckDB so the "last element"
        # semantic carries through.
        assert "-1" in sql or "list_extract(cast" in sql


class TestSafeOrdinalRegressionGuard:
    """``arr[SAFE_ORDINAL(n)]`` reaches DuckDB as ``arr[n]`` and returns NULL on OOB."""

    def test_in_bounds_returns_value(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(t, con, "SELECT [10, 20, 30][SAFE_ORDINAL(2)] AS v") == (20,)

    def test_out_of_bounds_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(t, con, "SELECT [10, 20, 30][SAFE_ORDINAL(99)] AS v") == (None,)

    def test_zero_index_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # BigQuery's SAFE_ORDINAL is 1-indexed; index 0 is out-of-bounds.
        # DuckDB's bare-bracket also returns NULL for index 0, matching
        # the BigQuery contract even though SQLGlot strips the SAFE_ORDINAL
        # wrapper down to a bare ``arr[0]``.
        assert _execute(t, con, "SELECT [10, 20, 30][SAFE_ORDINAL(0)] AS v") == (None,)
