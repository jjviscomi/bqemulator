"""Tests for SAFE_* math function translation.

SAFE_DIVIDE and SAFE_CAST are handled natively by SQLGlot's BigQuery →
DuckDB transpiler. SAFE_ADD / SAFE_SUBTRACT / SAFE_MULTIPLY /
SAFE_NEGATE require our custom rules — each wraps the underlying
arithmetic in DuckDB's ``TRY(...)`` so overflow surfaces as ``NULL``
instead of raising ``OutOfRangeException``.

We test both paths here to verify end-to-end correctness against a
real DuckDB connection.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit

_INT64_MIN = -9223372036854775808
_INT64_MAX = 9223372036854775807


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


class TestSafeDivide:
    """SQLGlot handles SAFE_DIVIDE natively — verify it works end-to-end."""

    def test_transpiles_to_case(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_DIVIDE(a, b) FROM t")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_DIVIDE" not in upper
        assert "CASE" in upper

    def test_executes_correctly(self, t: SQLTranslator) -> None:
        result = t.translate(
            "SELECT SAFE_DIVIDE(10.0, 0) AS zero_case, SAFE_DIVIDE(10.0, 4) AS normal_case",
        )
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None  # 10/0 → NULL
        assert row[1] == 2.5  # 10/4 → 2.5


class TestSafeCast:
    """SQLGlot handles SAFE_CAST → TRY_CAST natively."""

    def test_transpiles_to_try_cast(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_CAST('abc' AS INT64)")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_CAST" not in upper
        assert "TRY_CAST" in upper

    def test_executes_correctly(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_CAST('abc' AS INT64)")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None  # 'abc' cannot be cast to INT64


class TestSafeAdd:
    """Custom rule: ``exp.SafeAdd`` → ``TRY(a + b)``."""

    def test_transpiles_to_try(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_ADD(1, 2)")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_ADD" not in upper
        assert "TRY(" in upper

    def test_normal_add_returns_sum(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_ADD(1, 2) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (3,)

    def test_overflow_returns_null(self, t: SQLTranslator) -> None:
        result = t.translate(f"SELECT SAFE_ADD({_INT64_MAX}, 1) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (None,)

    def test_just_below_overflow(self, t: SQLTranslator) -> None:
        result = t.translate(f"SELECT SAFE_ADD({_INT64_MAX - 1}, 1) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (_INT64_MAX,)


class TestSafeSubtract:
    """Custom rule: ``exp.SafeSubtract`` → ``TRY(a - b)``."""

    def test_transpiles_to_try(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_SUBTRACT(5, 3)")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_SUBTRACT" not in upper
        assert "TRY(" in upper

    def test_normal_subtract(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_SUBTRACT(5, 3) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (2,)

    def test_overflow_returns_null(self, t: SQLTranslator) -> None:
        result = t.translate(f"SELECT SAFE_SUBTRACT({_INT64_MIN}, 1) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (None,)


class TestSafeMultiply:
    """Custom rule: ``exp.SafeMultiply`` → ``TRY(a * b)``."""

    def test_transpiles_to_try(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_MULTIPLY(3, 4)")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_MULTIPLY" not in upper
        assert "TRY(" in upper

    def test_normal_multiply(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_MULTIPLY(3, 4) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (12,)

    def test_overflow_returns_null(self, t: SQLTranslator) -> None:
        result = t.translate(f"SELECT SAFE_MULTIPLY({_INT64_MAX}, 2) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (None,)


class TestSafeNegate:
    """Custom rule: ``exp.SafeNegate`` → ``TRY(0 - a)``.

    The ``0 - a`` form (rather than ``-a``) is required so that
    ``SAFE_NEGATE(INT64_MIN)`` overflows BIGINT and ``TRY`` returns
    ``NULL`` — ``-INT64_MIN`` silently promotes to ``HUGEINT`` and
    would otherwise yield ``9223372036854775808``.
    """

    def test_transpiles_to_try(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_NEGATE(42)")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "SAFE_NEGATE" not in upper
        assert "TRY(" in upper

    def test_normal_negate(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_NEGATE(42) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (-42,)

    def test_with_column_reference(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_NEGATE(amount) FROM t")
        assert isinstance(result, Ok)
        assert "SAFE_NEGATE" not in result.value.upper()

    def test_null_input_returns_null(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SAFE_NEGATE(NULL) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (None,)

    def test_int64_min_overflow_returns_null(self, t: SQLTranslator) -> None:
        result = t.translate(f"SELECT SAFE_NEGATE({_INT64_MIN}) AS x")
        assert isinstance(result, Ok)
        conn = duckdb.connect()
        row = conn.execute(result.value).fetchone()
        conn.close()
        assert row == (None,)
