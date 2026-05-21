"""Tests for the miscellaneous Bucket J translation rules + rewriters.

Covers:

* :class:`bqemulator.sql.rules.misc_helpers.IeeeDivideRule`,
  ``FarmFingerprintRule``, ``RangeBucketRule``, ``ApproxTopSumRule``.
* :func:`bqemulator.sql.rewriter.aggregate_variants.rewrite_aggregate_variants`
  — ``ARRAY_AGG ORDER BY LIMIT n``, ``STRING_AGG ORDER BY LIMIT n``,
  ``ARRAY_AGG IGNORE NULLS``.
* :func:`bqemulator.sql.rewriter.numeric_literals.rewrite_numeric_literals`
  — NUMERIC / BIGNUMERIC typed literal precision pinning.
* :func:`bqemulator.sql.rewriter.sha512.rewrite_sha512` — pre-translator
  SHA512 → ``bqemu_sha512`` routing.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import math

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
    connection = duckdb.connect()
    connection.execute("INSTALL json; LOAD json;")
    register_builtin_udfs(connection)
    return connection


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestIeeeDivideRule:
    """``IEEE_DIVIDE`` → float division — yields ``±Inf`` instead of raising."""

    def test_div_by_zero_returns_inf(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(1.0, 0.0) AS x")
        assert row is not None
        assert math.isinf(row[0])
        assert row[0] > 0

    def test_negative_div_by_zero_returns_neg_inf(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(-1.0, 0.0) AS x")
        assert row is not None
        assert math.isinf(row[0])
        assert row[0] < 0

    def test_normal_div_returns_float(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT IEEE_DIVIDE(10.0, 4.0) AS x")
        assert row == (2.5,)


class TestRangeBucketRule:
    """``RANGE_BUCKET`` returns the count of boundaries ≤ point."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT RANGE_BUCKET(5, [10, 20, 30]) AS b", (0,)),
            ("SELECT RANGE_BUCKET(15, [10, 20, 30]) AS b", (1,)),
            ("SELECT RANGE_BUCKET(25, [10, 20, 30]) AS b", (2,)),
            ("SELECT RANGE_BUCKET(35, [10, 20, 30]) AS b", (3,)),
            # Boundary-equality lands in the bucket *after* the matching
            # boundary: 10 ≤ 10, 20 > 10, so bucket index 1.
            ("SELECT RANGE_BUCKET(10, [10, 20, 30]) AS b", (1,)),
        ],
    )
    def test_each_position(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str, expected: tuple
    ) -> None:
        assert _execute(t, con, sql) == expected


class TestRangeBucketNullPropagation:
    """``RANGE_BUCKET`` propagates NULL inputs per BigQuery's contract.

    The P8.b NULL-propagation closure wraps the
    ``len(list_filter(boundaries, x -> x <= point))`` happy-path
    expression in a ``CASE`` that returns NULL when either input is
    NULL — matching BigQuery's contract and pinning the
    ``standard_functions/math_range_bucket_null`` conformance fixture.
    """

    def test_null_point_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT RANGE_BUCKET(CAST(NULL AS INT64), [10, 20, 30]) AS b")
        assert row == (None,)

    def test_null_boundaries_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(
            t,
            con,
            "SELECT RANGE_BUCKET(15, CAST(NULL AS ARRAY<INT64>)) AS b",
        )
        assert row == (None,)

    def test_empty_boundaries_returns_zero(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # Empty array is NOT NULL — the happy-path branch fires and
        # returns 0 (no boundaries ≤ point).
        row = _execute(t, con, "SELECT RANGE_BUCKET(15, ARRAY<INT64>[]) AS b")
        assert row == (0,)


class TestSignFloatTypeRule:
    """``SIGN(<float_arg>)`` returns FLOAT64 with NaN propagation.

    The P8.b SIGN type-preservation + NaN-propagation closure wraps
    ``SIGN(<float_cast>)`` in ``CASE WHEN isnan(arg) THEN arg ELSE
    CAST(SIGN(arg) AS DOUBLE) END`` so the result column type stays
    FLOAT64 (DuckDB's bare ``SIGN`` returns TINYINT) and NaN
    propagates per BigQuery's contract (``SIGN(NaN) = NaN``, not 0).
    """

    def test_positive_infinity_returns_positive_one(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT SIGN(CAST('Infinity' AS FLOAT64)) AS s")
        assert row == (1.0,)
        assert isinstance(row[0], float)

    def test_negative_infinity_returns_negative_one(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT SIGN(CAST('-Infinity' AS FLOAT64)) AS s")
        assert row == (-1.0,)
        assert isinstance(row[0], float)

    def test_nan_propagates_to_nan(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT SIGN(CAST('NaN' AS FLOAT64)) AS s")
        assert row is not None
        assert math.isnan(row[0])
        assert isinstance(row[0], float)

    def test_null_float_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT SIGN(CAST(NULL AS FLOAT64)) AS s")
        assert row == (None,)

    def test_int_input_stays_integer(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # Sanity check: the rule does NOT fire for INT64 inputs; the
        # original DuckDB ``SIGN`` (returning TINYINT-coerced) flows
        # through and the happy-path INT64 surface stays intact.
        row = _execute(t, con, "SELECT SIGN(CAST(-7 AS INT64)) AS s")
        assert row == (-1,)


class TestCountIfEmptyZeroRule:
    """``COUNTIF`` returns 0 for empty input (not NULL).

    The P8.b empty-input closure wraps the typed ``CountIf`` node in
    ``COALESCE(..., 0)`` so the result of ``COUNTIF(p)`` over an empty
    source matches BigQuery's always-INT64-never-NULL contract.
    """

    def test_empty_source_returns_zero(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        con.execute("CREATE TABLE empty_t (v INT64)")
        row = _execute(t, con, "SELECT COUNTIF(v > 0) AS n FROM empty_t")
        assert row == (0,)

    def test_non_empty_source_returns_count(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        con.execute("CREATE TABLE pos_t (v INT64)")
        con.execute("INSERT INTO pos_t (v) VALUES (1), (2), (-1), (3), (-2)")
        row = _execute(t, con, "SELECT COUNTIF(v > 0) AS n FROM pos_t")
        assert row == (3,)


class TestFarmFingerprintRule:
    """``FARM_FINGERPRINT`` routes through the Python helper."""

    def test_deterministic(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        a = _execute(t, con, "SELECT FARM_FINGERPRINT('seed-42') AS h")
        b = _execute(t, con, "SELECT FARM_FINGERPRINT('seed-42') AS h")
        assert a == b
        assert a is not None
        assert isinstance(a[0], int)


class TestApproxTopSumRule:
    """``APPROX_TOP_SUM`` collapses to ``approx_top_k`` (length contract only)."""

    def test_array_length_matches_k(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("CREATE TABLE t (n INT, x INT)")
        con.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30), (4, 40)")
        row = _execute(t, con, "SELECT ARRAY_LENGTH(APPROX_TOP_SUM(n, x, 3)) AS n FROM t")
        assert row == (3,)


class TestArrayAggOrderByLimitRewriter:
    """``ARRAY_AGG(x ORDER BY k LIMIT n)`` → ``array_slice(array_agg(...), 1, n)``."""

    def test_keeps_first_n_in_order(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("CREATE TABLE t (n INT, label VARCHAR)")
        con.execute("INSERT INTO t VALUES (1, 'a'), (5, 'b'), (3, 'c'), (8, 'd'), (10, 'e')")
        row = _execute(
            t,
            con,
            "SELECT ARRAY_AGG(label ORDER BY n DESC LIMIT 3) AS arr FROM t",
        )
        assert row == (["e", "d", "b"],)


class TestStringAggOrderByLimitRewriter:
    """``STRING_AGG(x, sep ORDER BY k LIMIT n)`` rewrites to a sliced array_to_string."""

    def test_returns_joined_first_n(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("CREATE TABLE t (n INT)")
        con.execute("INSERT INTO t VALUES (1), (2), (3), (4), (5)")
        row = _execute(
            t,
            con,
            "SELECT STRING_AGG(CAST(n AS STRING), ',' ORDER BY n LIMIT 3) AS s FROM t",
        )
        assert row == ("1,2,3",)


class TestArrayAggIgnoreNullsRewriter:
    """``ARRAY_AGG(expr IGNORE NULLS …)`` → ``ARRAY_AGG(expr …) FILTER (WHERE expr IS NOT NULL)``.

    The rewriter preserves BigQuery's null-skipping aggregate
    semantic that SQLGlot's DuckDB transpile silently drops.
    """

    def test_filter_strips_nulls(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("CREATE TABLE events (event_type VARCHAR, user_id INT)")
        con.execute(
            "INSERT INTO events VALUES "
            "('purchase', 1), ('purchase', 2), ('view', 3), "
            "('purchase', 4), ('purchase', 5), ('purchase', 5)"
        )
        row = _execute(
            t,
            con,
            "SELECT ARRAY_AGG(IF(event_type = 'purchase', user_id, NULL) "
            "IGNORE NULLS ORDER BY user_id) AS buyers FROM events",
        )
        assert row == ([1, 2, 4, 5, 5],)


class TestSha512Rule:
    """``SHA512(x)`` → ``bqemu_sha512(x)`` (pre-translator)."""

    def test_known_vector_matches_hashlib(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT TO_HEX(SHA512('hello')) AS h")
        assert row == (hashlib.sha512(b"hello").hexdigest(),)

    def test_translator_routes_through_helper(self, t: SQLTranslator) -> None:
        # The pre-translator must fire BEFORE SQLGlot's BQ → DuckDB
        # transpile collapses ``SHA512`` to ``SHA256``. We verify the
        # bqemu helper name appears and that ``SHA256`` does not.
        result = t.translate("SELECT TO_HEX(SHA512('hello')) AS h")
        assert isinstance(result, Ok)
        assert "BQEMU_SHA512" in result.value.upper()
        assert "SHA256" not in result.value.upper()

    def test_null_input_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``TO_HEX(NULL)`` propagates as ``NULL``.
        assert _execute(t, con, "SELECT TO_HEX(SHA512(CAST(NULL AS STRING))) AS h") == (None,)

    def test_no_sha512_short_circuits(self, t: SQLTranslator) -> None:
        # When the query has no SHA512 reference the rewriter must
        # leave the SQL alone (the short-circuit path).
        result = t.translate("SELECT TO_HEX(SHA256('hello')) AS h")
        assert isinstance(result, Ok)
        assert "BQEMU_SHA512" not in result.value.upper()


class TestNumericLiteralRewriter:
    """NUMERIC / BIGNUMERIC literals get explicit DECIMAL precision."""

    def test_numeric_max(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT NUMERIC '99999999999999999999999999999.999999999' AS n",
        )
        assert row == (Decimal("99999999999999999999999999999.999999999"),)

    def test_bignumeric_small_fits(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # 1 integer + 38 fractional digits fits DECIMAL(38, 38).
        row = _execute(
            t,
            con,
            "SELECT BIGNUMERIC '0.34992332820282019728792003956564819967' AS n",
        )
        assert row == (Decimal("0.34992332820282019728792003956564819967"),)

    def test_word_boundary_does_not_match_identifier(self, t: SQLTranslator) -> None:
        # An identifier ending in NUMERIC (``MY_NUMERIC 'x'``) must be
        # left alone — the regex anchors on a word boundary.
        result = t.translate("SELECT my_col AS my_numeric FROM t")
        assert isinstance(result, Ok)
        # ``DECIMAL(38, 9)`` does not appear if the rule correctly
        # skips the identifier.
        assert "DECIMAL(38, 9)" not in result.value.upper()

    def test_bignumeric_overflow_truncates_fractional_high_precision(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """1 int + 39 frac digits overflows; Path C drops one fractional digit."""
        # Literal is 1.234567890123456789012345678901234567890 (1 int + 39 frac
        # = 40 total). DuckDB's DECIMAL(38, ...) cap forces a 1-digit
        # fractional truncation — the trailing '0' is dropped, the value
        # is preserved bit-exact at 37 fractional digits, and the
        # rendered Decimal matches the truncated literal.
        row = _execute(
            t,
            con,
            "SELECT BIGNUMERIC '1.234567890123456789012345678901234567890' AS n",
        )
        # Decimal canonicalises trailing zeros, so the equality compares
        # the truncated 37-digit fractional form.
        assert row == (Decimal("1.2345678901234567890123456789012345678"),)

    def test_bignumeric_overflow_truncates_fractional_wide_integer(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """38 int + 9 frac digits overflows; Path C drops the entire fractional."""
        # Literal is 12345678901234567890123456789012345678.123456789
        # (38 int + 9 frac = 47 total). max_scale = 38 - 38 = 0 → drop
        # all 9 fractional digits, leaving the integer-only value.
        row = _execute(
            t,
            con,
            "SELECT BIGNUMERIC '12345678901234567890123456789012345678.123456789' AS n",
        )
        assert row == (Decimal(12345678901234567890123456789012345678),)

    def test_bignumeric_overflow_mid_range_int_frac(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        """20 int + 25 frac digits overflows; Path C truncates to 18 frac."""
        # Literal is 12345678901234567890.1234567890123456789012345
        # (20 int + 25 frac = 45 total). max_scale = 38 - 20 = 18 → keep
        # 18 fractional digits, drop the last 7.
        row = _execute(
            t,
            con,
            "SELECT BIGNUMERIC '12345678901234567890.1234567890123456789012345' AS n",
        )
        assert row == (Decimal("12345678901234567890.123456789012345678"),)

    def test_bignumeric_max_value_still_xfails(self) -> None:
        """BIGNUMERIC max (39 int) cannot be represented and stays an error.

        Documents the unchanged contract for the
        ``standard_functions/bound_bignumeric_max`` fixture: 39 integer
        digits exceed ``DECIMAL(38, 0)`` even after Path C's fractional
        truncation, so the literal falls through to ``bqemu_to_bignumeric``
        and the Python helper raises ``Invalid BIGNUMERIC literal``.
        """
        # Rewritten SQL routes through ``bqemu_to_bignumeric`` (Path B
        # fallback), which would raise at runtime. Verify the routing
        # decision here so a future refactor that silently truncates
        # the integer part (which would corrupt the value) fails the
        # test rather than masking the divergence.
        from bqemulator.sql.rewriter.numeric_literals import rewrite_numeric_literals

        rewritten = rewrite_numeric_literals(
            "SELECT BIGNUMERIC '578960446186580977117854925043439539266"
            ".34992332820282019728792003956564819967' AS n",
        )
        assert "bqemu_to_bignumeric" in rewritten
        # Specifically verify the literal flows through with the
        # integer part intact (NOT truncated to 38 digits) — silent
        # integer truncation would change the value and is explicitly
        # forbidden.
        assert "578960446186580977117854925043439539266" in rewritten
