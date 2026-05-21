"""Tests for the BigQuery decimal-literal pre-translator rewriter.

The rewriter rewrites bare decimal literals (``3.25``) to scientific
notation (``3.25e0``) before the SQLGlot transpile so DuckDB types them
as ``DOUBLE`` instead of inferring a narrow ``DECIMAL(p, s)`` that
surfaces as NUMERIC on the BigQuery REST wire.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.decimal_literals import rewrite_decimal_literals
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


class TestRewriteDecimalLiterals:
    """Direct tests of the rewriter function."""

    def test_rewrites_bare_decimal_literal(self) -> None:
        out = rewrite_decimal_literals("SELECT 3.25 AS x")
        assert "3.25e0" in out.lower()

    def test_rewrites_negative_decimal(self) -> None:
        out = rewrite_decimal_literals("SELECT -1.5 AS x")
        assert "1.5e0" in out.lower()

    def test_rewrites_multiple_decimals(self) -> None:
        out = rewrite_decimal_literals("SELECT 2.5 AS x, 3.5 AS y")
        assert out.lower().count("e0") == 2

    def test_integer_literal_untouched(self) -> None:
        out = rewrite_decimal_literals("SELECT 5 AS x")
        assert "e0" not in out.lower()
        assert "5" in out

    def test_string_literal_with_dot_untouched(self) -> None:
        out = rewrite_decimal_literals("SELECT 'abc 3.25 def' AS s")
        assert "3.25e0" not in out.lower()

    def test_numeric_typed_literal_untouched(self) -> None:
        # ``NUMERIC '3.25'`` is a string literal — the rewriter must not
        # touch it. (The numeric_literals.py rewriter handles it
        # separately.)
        out = rewrite_decimal_literals("SELECT NUMERIC '3.25' AS n")
        assert "3.25e0" not in out.lower()

    def test_scientific_literal_untouched(self) -> None:
        # Already in scientific form — no double-rewrite.
        out = rewrite_decimal_literals("SELECT 3.25e0 AS x")
        # exactly one ``e0`` in the output (no e0e0 etc.)
        assert out.lower().count("e0") == 1

    def test_no_decimal_passthrough(self) -> None:
        # Common path — no ``.`` in the SQL: rewriter returns input unchanged.
        sql = "SELECT a, b FROM t"
        assert rewrite_decimal_literals(sql) == sql

    def test_unparseable_sql_returns_input(self) -> None:
        # The rewriter is best-effort — parse failures fall through.
        sql = "NOT A VALID 3.5 SQL"
        # Should NOT raise.
        assert rewrite_decimal_literals(sql) is not None


class TestEndToEnd:
    """End-to-end behaviour through the translator + DuckDB."""

    @pytest.fixture
    def t(self) -> SQLTranslator:
        return SQLTranslator()

    @pytest.fixture
    def con(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect()

    def test_bare_decimal_lands_as_double(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT 3.25 AS x")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert desc[0][1] == "DOUBLE"

    def test_ceil_of_decimal_lands_as_double(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``CEIL(3.2)`` over DECIMAL(2, 1) returns DECIMAL in DuckDB; the
        # rewrite to scientific notation makes the input DOUBLE so CEIL
        # also returns DOUBLE.
        result = t.translate("SELECT CEIL(3.2) AS x")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert desc[0][1] == "DOUBLE"

    def test_round_two_arg_decimal_lands_as_double(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT ROUND(3.14159, 2) AS x")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert desc[0][1] == "DOUBLE"

    def test_numeric_literal_remains_decimal(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``NUMERIC '3.25'`` stays a typed-literal cast — the
        # decimal-literal rewriter must not steal the cast.
        result = t.translate("SELECT NUMERIC '100.00' AS n")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        # DECIMAL(38, 9) per numeric_literals.py rewriter.
        assert "DECIMAL" in str(desc[0][1])

    def test_integer_literal_untouched_end_to_end(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT 42 AS x")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        # DuckDB types ``42`` as INTEGER (smallest fit), not DOUBLE.
        assert "INT" in str(desc[0][1])
