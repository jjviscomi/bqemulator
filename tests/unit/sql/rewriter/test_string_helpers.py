"""Unit tests for the string-helpers pre-translator rewriter.

The rewriter in :mod:`bqemulator.sql.rewriter.string_helpers` handles:

* ``NORMALIZE`` / ``NORMALIZE_AND_CASEFOLD`` → routed to Python helpers
  while the ``is_casefold`` flag is still visible on the AST.
* ``INSTR(haystack, needle, position, occurrence)`` — the 4-arg form is
  routed to the Python helper while the ``occurrence`` argument is
  still on the AST (SQLGlot drops it downstream).

These tests pin the short-circuits, parse-failure tolerance, and the
internal helpers' coverage of unrecognised form operands.
"""

from __future__ import annotations

import pytest
from sqlglot import exp

from bqemulator.sql.rewriter.string_helpers import _form_keyword, rewrite_string_helpers

pytestmark = pytest.mark.unit


class TestShortCircuit:
    """The rewriter is a no-op when neither NORMALIZE nor INSTR appears."""

    def test_unrelated_sql_returns_identity(self) -> None:
        """SQL without NORMALIZE / INSTR returns the input object unchanged."""
        sql = "SELECT 1, 'x', 3.14"
        assert rewrite_string_helpers(sql) is sql


class TestParseFailureTolerance:
    """Parse failures fall through unchanged."""

    def test_invalid_sql_with_normalize_token_returned_as_is(self) -> None:
        """Garbage SQL with NORMALIZE trigger token returned unchanged."""
        sql = "NORMALIZE ((( garbage"
        assert rewrite_string_helpers(sql) == sql

    def test_invalid_sql_with_instr_token_returned_as_is(self) -> None:
        """Garbage SQL with INSTR trigger token returned unchanged."""
        sql = "INSTR ((( garbage"
        assert rewrite_string_helpers(sql) == sql


class TestNormalizeRewrite:
    """``NORMALIZE`` / ``NORMALIZE_AND_CASEFOLD`` → ``bqemu_normalize{,_casefold}``."""

    def test_normalize_default_form_nfc(self) -> None:
        """NORMALIZE without an explicit form defaults to NFC."""
        sql = "SELECT NORMALIZE('Straße')"
        out = rewrite_string_helpers(sql)
        assert "bqemu_normalize" in out.lower()
        # Default form embedded as literal.
        assert "'NFC'" in out

    def test_normalize_with_explicit_nfkc(self) -> None:
        """NORMALIZE(s, NFKC) carries the form keyword through."""
        sql = "SELECT NORMALIZE('café', NFKC)"
        out = rewrite_string_helpers(sql)
        assert "bqemu_normalize" in out.lower()
        assert "'NFKC'" in out

    def test_normalize_and_casefold_dispatches_to_casefold_helper(self) -> None:
        """NORMALIZE_AND_CASEFOLD picks the casefold helper variant."""
        sql = "SELECT NORMALIZE_AND_CASEFOLD('Straße', NFKD)"
        out = rewrite_string_helpers(sql)
        assert "bqemu_normalize_casefold" in out.lower()
        assert "'NFKD'" in out

    def test_unknown_form_keyword_defaults_to_nfc(self) -> None:
        """An unrecognised Var-shape form operand falls back to NFC default."""
        # Use an identifier that isn't one of the four recognised form
        # keywords — _form_keyword returns None and the default NFC is
        # used. SQLGlot parses the Var-form (no quotes).
        sql = "SELECT NORMALIZE('s', NOT_VALID)"
        out = rewrite_string_helpers(sql)
        assert "bqemu_normalize" in out.lower()
        assert "'NFC'" in out


class TestInstrRewrite:
    """4-argument ``INSTR`` is routed to ``bqemu_instr_occurrence``."""

    def test_4arg_instr_rewritten(self) -> None:
        """INSTR(haystack, needle, position, occurrence) routes to UDF."""
        sql = "SELECT INSTR('abcabc', 'b', 1, 2)"
        out = rewrite_string_helpers(sql)
        assert "bqemu_instr_occurrence" in out.lower()

    def test_3arg_instr_unchanged(self) -> None:
        """3-arg INSTR is left alone — DuckDB handles it natively."""
        sql = "SELECT INSTR('abcabc', 'b', 1)"
        out = rewrite_string_helpers(sql)
        assert "bqemu_instr_occurrence" not in out.lower()

    def test_2arg_instr_unchanged(self) -> None:
        """2-arg INSTR is left alone — DuckDB handles it natively."""
        sql = "SELECT INSTR('abcabc', 'b')"
        out = rewrite_string_helpers(sql)
        assert "bqemu_instr_occurrence" not in out.lower()


class TestNoModification:
    """When trigger tokens appear but no node matches, return input identity."""

    def test_normalize_substring_in_identifier_only(self) -> None:
        """The token NORMALIZE appearing only inside an identifier doesn't fire."""
        # The substring "NORMALIZE" is present but only inside a column
        # name — the parser yields no Normalize node, so the rewriter
        # walks but doesn't modify anything.
        sql = "SELECT normalize_count FROM tbl"
        out = rewrite_string_helpers(sql)
        # No replacement performed; returns original string unchanged.
        assert out == sql

    def test_instr_substring_in_identifier_only(self) -> None:
        """The token INSTR appearing only inside an identifier doesn't fire."""
        sql = "SELECT instr_value FROM tbl"
        out = rewrite_string_helpers(sql)
        assert out == sql


class TestFormKeywordHelper:
    """Direct coverage of the private ``_form_keyword`` helper.

    The helper dispatches on the form-operand AST shape; some branches
    (None input, string-literal form) aren't reachable through normal
    BigQuery SQL because SQLGlot rejects the corresponding source.
    """

    def test_none_returns_none(self) -> None:
        """Passing None returns None (the early-return short-circuit)."""
        assert _form_keyword(None) is None

    def test_string_literal_recognised_form_returns_uppered(self) -> None:
        """A string-literal form operand with a known keyword is uppered."""
        # SQLGlot's BigQuery dialect rejects quoted form operands, but
        # the helper handles them defensively (other dialects may
        # produce a Literal-shape).
        assert _form_keyword(exp.Literal.string("nfc")) == "NFC"

    def test_string_literal_unknown_returns_none(self) -> None:
        """An unrecognised string-literal form is dropped to None."""
        assert _form_keyword(exp.Literal.string("garbage")) is None

    def test_unsupported_node_type_returns_none(self) -> None:
        """A form operand of an unsupported shape returns None."""
        # An Add node has no Var / Column / Literal-string shape; the
        # helper returns None.
        node = exp.Add(this=exp.Literal.number(1), expression=exp.Literal.number(2))
        assert _form_keyword(node) is None
