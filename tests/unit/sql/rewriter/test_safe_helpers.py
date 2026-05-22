"""Unit tests for the ``SAFE.`` prefix pre-translator rewriter.

The rewriter in :mod:`bqemulator.sql.rewriter.safe_helpers` unwraps
BigQuery's ``SAFE.X(args)`` into ``TRY(X(args))`` so the post-translate
pipeline doesn't mangle the ``SAFE`` schema-qualified token into a
project-prefixed identifier.

These tests pin the short-circuit, parse-failure tolerance, and the
no-modification path when the trigger token is only inside an
unrelated string literal.
"""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.safe_helpers import rewrite_safe_helpers

pytestmark = pytest.mark.unit


class TestShortCircuit:
    """The rewriter is a no-op when ``SAFE.`` isn't referenced."""

    def test_no_safe_prefix_returns_identity(self) -> None:
        """SQL without SAFE. returns the input object unchanged."""
        sql = "SELECT 1, 'x'"
        assert rewrite_safe_helpers(sql) is sql


class TestParseFailureTolerance:
    """Parse failures fall through unchanged."""

    def test_invalid_sql_with_safe_token_returned_as_is(self) -> None:
        """Garbage SQL containing 'SAFE.' returned unchanged."""
        sql = "SAFE. ((( garbage"
        assert rewrite_safe_helpers(sql) == sql


class TestRewriteSafePrefix:
    """``SAFE.X(args)`` → ``TRY(X(args))``."""

    def test_safe_ln_rewritten_to_try(self) -> None:
        """SAFE.LN(x) becomes TRY(LN(x))."""
        sql = "SELECT SAFE.LN(-1)"
        out = rewrite_safe_helpers(sql)
        upper = out.upper()
        assert "TRY(" in upper
        # SAFE. prefix is removed from the call.
        assert "SAFE.LN" not in upper

    def test_safe_sqrt_rewritten_to_try(self) -> None:
        """SAFE.SQRT(x) becomes TRY(SQRT(x))."""
        sql = "SELECT SAFE.SQRT(-1)"
        out = rewrite_safe_helpers(sql)
        assert "TRY(" in out.upper()


class TestNoModification:
    """Trigger present but no SafeFunc node — return input unchanged."""

    def test_safe_token_in_string_literal_only(self) -> None:
        """``'SAFE.'`` inside a string literal doesn't trigger the rewrite."""
        # The trigger token is present in a string literal, so the
        # upper() check matches; SQLGlot parses but yields no SafeFunc
        # nodes, so the rewriter exits via the no-modification branch.
        sql = "SELECT 'SAFE.text' AS lbl"
        out = rewrite_safe_helpers(sql)
        # The string is unchanged (returned via the no-modification path).
        assert out == sql
