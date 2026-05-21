"""Tests for the ``bqemu_to_bignumeric`` builtin UDF (ADR 0023 §1.B).

The UDF is the runtime backing for ``BIGNUMERIC '…'`` typed literals
and ``PARSE_BIGNUMERIC(s)`` calls. Its declared DuckDB return type is
``DECIMAL(38, 10)`` so the schema-renderer's "scale > 9 → BIGNUMERIC"
rule fires; the Python body uses :class:`Decimal` so the integer
capacity is decoupled from DuckDB's default DECIMAL(18, 3).
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from bqemulator.sql.builtin_udfs import bqemu_to_bignumeric, register_builtin_udfs

pytestmark = pytest.mark.unit


class TestBqemuToBignumericDirect:
    """Direct tests of the Python helper."""

    def test_none_propagates(self) -> None:
        assert bqemu_to_bignumeric(None) is None

    def test_parses_simple_decimal(self) -> None:
        assert bqemu_to_bignumeric("123.456") == Decimal("123.456")

    def test_parses_negative_decimal(self) -> None:
        assert bqemu_to_bignumeric("-3.14") == Decimal("-3.14")

    def test_parses_integer(self) -> None:
        assert bqemu_to_bignumeric("100") == Decimal(100)

    def test_parses_wide_decimal(self) -> None:
        value = "12345678901234567890.123456789"
        assert bqemu_to_bignumeric(value) == Decimal(value)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid BIGNUMERIC literal"):
            bqemu_to_bignumeric("not-a-number")


class TestBqemuToBignumericRegistered:
    """End-to-end tests of the UDF registered on a DuckDB connection."""

    @pytest.fixture
    def con(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect()
        register_builtin_udfs(conn)
        return conn

    def test_declared_return_type_is_decimal_38_10(self, con: duckdb.DuckDBPyConnection) -> None:
        # The declared return type is what the cursor description
        # carries — the schema renderer's "scale > 9 → BIGNUMERIC"
        # rule reads it.
        desc = con.execute("SELECT bqemu_to_bignumeric('123.456') AS n").description
        assert "DECIMAL(38,10)" in str(desc[0][1])

    def test_round_trip_wide_value(self, con: duckdb.DuckDBPyConnection) -> None:
        row = con.execute(
            "SELECT bqemu_to_bignumeric('12345678901234567890.123456789') AS n",
        ).fetchone()
        # Decimal canonicalises trailing zeros; the UDF returns the
        # exact parsed value.
        assert row[0] == Decimal("12345678901234567890.123456789")

    def test_null_propagates(self, con: duckdb.DuckDBPyConnection) -> None:
        row = con.execute("SELECT bqemu_to_bignumeric(NULL::VARCHAR) AS n").fetchone()
        assert row[0] is None
