"""Tests for the PARSE_NUMERIC / PARSE_BIGNUMERIC translation rules."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.builtin_udfs import register_builtin_udfs
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    register_builtin_udfs(conn)
    return conn


class TestParseNumeric:
    """``PARSE_NUMERIC(s)`` → ``CAST(s AS DECIMAL(38, 9))``."""

    def test_rewrites_to_cast(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT PARSE_NUMERIC('123.456') AS n")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "PARSE_NUMERIC" not in upper
        assert "DECIMAL(38, 9)" in upper

    def test_returns_decimal(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate("SELECT PARSE_NUMERIC('123.456') AS n")
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row[0] == Decimal("123.456000000")
        desc = con.execute(result.value).description
        assert "DECIMAL(38,9)" in str(desc[0][1])

    def test_negative_value(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        result = t.translate("SELECT PARSE_NUMERIC('-123.456') AS n")
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row[0] == Decimal("-123.456")


class TestParseBignumeric:
    """``PARSE_BIGNUMERIC(s)`` → ``bqemu_to_bignumeric(s)`` (DECIMAL(38, 10))."""

    def test_rewrites_to_udf(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT PARSE_BIGNUMERIC('123.456') AS n")
        assert isinstance(result, Ok)
        assert "bqemu_to_bignumeric" in result.value.lower()

    def test_returns_bignumeric_scale(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # Output column type must be DECIMAL(38, 10) so the schema
        # renderer's "scale > 9 → BIGNUMERIC" rule fires.
        result = t.translate("SELECT PARSE_BIGNUMERIC('123.456') AS n")
        assert isinstance(result, Ok)
        desc = con.execute(result.value).description
        assert "DECIMAL(38,10)" in str(desc[0][1])

    def test_wide_integer_value(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 20 integer + 5 fractional digits — fits DECIMAL(38, 10)'s
        # 28 integer slots.
        result = t.translate("SELECT PARSE_BIGNUMERIC('12345678901234567890.12345') AS n")
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row[0] == Decimal("12345678901234567890.12345")


class TestBignumericLiteral:
    """``BIGNUMERIC 'literal'`` pre-rewriter → ``bqemu_to_bignumeric`` UDF call."""

    def test_routes_through_udf(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT BIGNUMERIC '123.456' AS n")
        assert isinstance(result, Ok)
        assert "bqemu_to_bignumeric" in result.value.lower()

    def test_wide_literal_executes_cleanly(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The 20-integer-digit literal previously failed under the
        # ``CAST AS DECIMAL(38, 38)`` rewrite (no room for integer
        # digits). The UDF returns DECIMAL(38, 10), which has 28
        # integer slots.
        result = t.translate("SELECT BIGNUMERIC '12345678901234567890.123456789' AS n")
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row[0] == Decimal("12345678901234567890.123456789")


class TestNumericLiteralUntouched:
    """``NUMERIC '…'`` literals keep the existing ``CAST AS DECIMAL(38, 9)`` rewrite."""

    def test_numeric_literal_remains_decimal(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        result = t.translate("SELECT NUMERIC '100.00' AS n")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "DECIMAL(38, 9)" in upper
        desc = con.execute(result.value).description
        assert "DECIMAL(38,9)" in str(desc[0][1])
