"""Unit tests for the SHA512 pre-translator rewriter.

The rewriter in :mod:`bqemulator.sql.rewriter.sha512` routes
BigQuery ``SHA512(x)`` calls through the Python helper
``bqemu_sha512(x)`` while the BigQuery AST still carries the
``length=512`` annotation (SQLGlot's BQ → DuckDB transpile mistakenly
drops the algorithm width).

These tests pin the short-circuit, parse-failure tolerance, and the
guard branches that skip non-512 widths and missing operands.
"""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.sha512 import rewrite_sha512

pytestmark = pytest.mark.unit


class TestShortCircuit:
    """The rewriter is a no-op when SHA512 isn't referenced."""

    def test_unrelated_sql_returns_identity(self) -> None:
        """SQL without SHA512 returns identity (no parse pass)."""
        sql = "SELECT 1, 'x'"
        assert rewrite_sha512(sql) is sql

    def test_sha256_not_touched(self) -> None:
        """A SHA256 reference doesn't trigger the SHA512-only token check."""
        sql = "SELECT SHA256('hello')"
        assert rewrite_sha512(sql) is sql


class TestParseFailureTolerance:
    """Parse failures fall through unchanged so downstream surfaces them."""

    def test_invalid_sql_with_sha512_token_returned_as_is(self) -> None:
        """Garbage SQL with SHA512 trigger token returned unchanged."""
        sql = "SHA512 ((( bad"
        assert rewrite_sha512(sql) == sql


class TestRewriteSha512:
    """``SHA512(x)`` → ``bqemu_sha512(x)`` when the AST has length=512."""

    def test_sha512_rewritten_to_helper(self) -> None:
        """SHA512('abc') is replaced by bqemu_sha512('abc')."""
        sql = "SELECT SHA512('abc')"
        out = rewrite_sha512(sql)
        assert "bqemu_sha512" in out.lower()

    def test_sha512_inside_to_hex_rewritten(self) -> None:
        """SHA512 wrapped in TO_HEX(...) still gets the helper substitution."""
        sql = "SELECT TO_HEX(SHA512('abc'))"
        out = rewrite_sha512(sql)
        assert "bqemu_sha512" in out.lower()


class TestNoModificationGuards:
    """Token match present but no eligible SHA2 node leaves SQL unchanged."""

    def test_sha512_token_in_identifier_only(self) -> None:
        """The token SHA512 inside a column identifier doesn't fire the rewrite."""
        # The substring "SHA512" is present in the identifier; the
        # parser yields a Column, not a SHA2 node, so the rewriter
        # walks but doesn't modify anything.
        sql = "SELECT sha512_col FROM tbl"
        out = rewrite_sha512(sql)
        assert out == sql

    def test_sha2_without_length_arg_skipped(self) -> None:
        """A SHA2(x) call without a length arg is skipped (length_node is None)."""
        # Embed the SHA512 trigger token in a string literal so the
        # token check passes; the underlying call is SHA2('abc') with
        # no length argument so the rewriter's length_node guard fires.
        sql = "SELECT 'SHA512_label' AS lbl, SHA2('abc') AS h"
        out = rewrite_sha512(sql)
        assert "bqemu_sha512" not in out.lower()

    def test_sha2_with_non_512_length_skipped(self) -> None:
        """A SHA2(x, 256) literal-length call is skipped (only 512 is rewritten)."""
        sql = "SELECT 'SHA512_label' AS lbl, SHA2('abc', 256) AS h"
        out = rewrite_sha512(sql)
        assert "bqemu_sha512" not in out.lower()
