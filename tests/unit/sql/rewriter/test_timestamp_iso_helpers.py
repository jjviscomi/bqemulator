"""Unit tests for the FORMAT_TIMESTAMP / PARSE_TIMESTAMP pre-translator rewriter.

The rewriter in :mod:`bqemulator.sql.rewriter.timestamp_iso_helpers`
routes BigQuery ``FORMAT_TIMESTAMP`` / ``PARSE_TIMESTAMP`` calls that
carry a ``zone`` argument or ``%Ez`` / ``%Z`` specifiers through the
Python-backed helpers in :mod:`bqemulator.sql.builtin_udfs`. These
tests pin the rewriter's short-circuits, parse-failure tolerance, and
the AST replacement paths for each shape.
"""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.timestamp_iso_helpers import rewrite_timestamp_iso_helpers

pytestmark = pytest.mark.unit


class TestShortCircuit:
    """The rewriter is a no-op when neither trigger token appears."""

    def test_no_trigger_returns_input_identity(self) -> None:
        """SQL without FORMAT_TIMESTAMP / PARSE_TIMESTAMP returns identity."""
        sql = "SELECT 1, 'x', 3.14"
        assert rewrite_timestamp_iso_helpers(sql) is sql

    def test_unrelated_timestamp_call_unchanged(self) -> None:
        """A naked TIMESTAMP literal doesn't trigger the rewrite."""
        sql = "SELECT TIMESTAMP '2024-01-15 12:00:00+00'"
        assert rewrite_timestamp_iso_helpers(sql) is sql


class TestParseFailureTolerance:
    """Parse failures fall through unchanged so downstream surfaces them."""

    def test_invalid_sql_with_format_timestamp_token_returned_as_is(self) -> None:
        """Garbage SQL still containing the trigger token is returned unchanged."""
        sql = "NOT SQL FORMAT_TIMESTAMP ((( garbage"
        assert rewrite_timestamp_iso_helpers(sql) == sql

    def test_invalid_sql_with_parse_timestamp_token_returned_as_is(self) -> None:
        """Garbage SQL with PARSE_TIMESTAMP trigger token returned unchanged."""
        sql = "PARSE_TIMESTAMP ))) bad"
        assert rewrite_timestamp_iso_helpers(sql) == sql


class TestFormatTimestampRewrite:
    """``FORMAT_TIMESTAMP`` with a zone arg or ``%E`` token is routed to the UDF."""

    def test_zone_argument_triggers_helper(self) -> None:
        """FORMAT_TIMESTAMP with a 3rd zone arg rewrites to bqemu_format_timestamp_iso."""
        sql = "SELECT FORMAT_TIMESTAMP('%Y-%m-%d', TIMESTAMP '2024-01-15 12:00:00+00', 'UTC')"
        out = rewrite_timestamp_iso_helpers(sql)
        assert "bqemu_format_timestamp_iso" in out.lower()

    def test_format_with_pct_ez_token_triggers_helper(self) -> None:
        """A format string with %Ez triggers the rewrite even without a zone arg."""
        sql = "SELECT FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%S%Ez', TIMESTAMP '2024-01-15 12:00:00+00')"
        out = rewrite_timestamp_iso_helpers(sql)
        assert "bqemu_format_timestamp_iso" in out.lower()

    def test_plain_format_timestamp_without_pct_e_unchanged(self) -> None:
        """FORMAT_TIMESTAMP without %E and without zone is left alone."""
        sql = "SELECT FORMAT_TIMESTAMP('%Y-%m-%d', TIMESTAMP '2024-01-15 12:00:00+00')"
        out = rewrite_timestamp_iso_helpers(sql)
        # No rewrite — call still in original form.
        assert "bqemu_format_timestamp_iso" not in out.lower()


class TestParseTimestampRewrite:
    """``PARSE_TIMESTAMP`` with ``%Ez`` / ``%Z`` is routed to the UDF."""

    def test_parse_with_pct_ez_triggers_helper(self) -> None:
        """%Ez in the format triggers the parse-side rewrite."""
        sql = "SELECT PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S%Ez', '2024-01-15T12:00:00+00:00')"
        out = rewrite_timestamp_iso_helpers(sql)
        assert "bqemu_parse_timestamp_iso" in out.lower()

    def test_parse_with_pct_uppercase_z_triggers_helper(self) -> None:
        """%Z named-zone token triggers the rewrite (DuckDB silently accepts ambiguous names)."""
        sql = "SELECT PARSE_TIMESTAMP('%Y-%m-%d %Z', '2024-01-15 UTC')"
        out = rewrite_timestamp_iso_helpers(sql)
        assert "bqemu_parse_timestamp_iso" in out.lower()

    def test_parse_without_pct_ez_or_z_unchanged(self) -> None:
        """PARSE_TIMESTAMP without %Ez / %Z is left alone."""
        sql = "SELECT PARSE_TIMESTAMP('%Y-%m-%d', '2024-01-15')"
        out = rewrite_timestamp_iso_helpers(sql)
        assert "bqemu_parse_timestamp_iso" not in out.lower()


class TestFormatNodeHelper:
    """Internal ``_format_has_ez_or_z`` covers the non-literal branch."""

    def test_format_helper_handles_column_arg_via_non_rewrite(self) -> None:
        """PARSE_TIMESTAMP with a column for the format argument is not rewritten."""
        # The format-arg is a Column reference (not a Literal). The
        # _format_has_ez_or_z helper returns False, so the rewriter
        # skips this call.
        sql = "SELECT PARSE_TIMESTAMP(fmt_col, ts_col) FROM t"
        out = rewrite_timestamp_iso_helpers(sql)
        # No bqemu_ helper inserted.
        assert "bqemu_parse_timestamp_iso" not in out.lower()
