"""Unit tests for the JSON helpers pre-translator rewriter.

The rewriter in :mod:`bqemulator.sql.rewriter.json_helpers` wraps
BigQuery's ``TO_JSON(value)`` in an explicit ``CAST(... AS JSON)`` so
the downstream SQLGlot transpile preserves the JSON-vs-STRING result
type (SQLGlot otherwise collapses both ``TO_JSON`` and
``TO_JSON_STRING`` to the same ``CAST(TO_JSON(...) AS TEXT)`` form).

These tests pin the short-circuit, parse-failure tolerance, and the
``already-wrapped`` skip path.
"""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.json_helpers import rewrite_json_helpers

pytestmark = pytest.mark.unit


class TestShortCircuit:
    """The rewriter is a no-op when no TO_JSON appears."""

    def test_unrelated_sql_returns_identity(self) -> None:
        """SQL without TO_JSON returns the input object unchanged."""
        sql = "SELECT 1, 'x'"
        assert rewrite_json_helpers(sql) is sql


class TestParseFailureTolerance:
    """Parse failures fall through unchanged."""

    def test_invalid_sql_with_to_json_token_returned_as_is(self) -> None:
        """Garbage SQL with TO_JSON trigger token returned unchanged."""
        sql = "TO_JSON ((( bad"
        assert rewrite_json_helpers(sql) == sql


class TestRewriteToJson:
    """``TO_JSON(x)`` is wrapped in ``CAST(... AS JSON)``."""

    def test_to_json_wraps_cast(self) -> None:
        """TO_JSON(x) is wrapped in CAST(TO_JSON(x) AS JSON)."""
        sql = "SELECT TO_JSON([1, 2, 3])"
        out = rewrite_json_helpers(sql)
        upper = out.upper()
        assert "CAST(" in upper
        assert "AS JSON" in upper


class TestAlreadyWrapped:
    """Calls already wrapped in ``CAST(... AS JSON)`` are skipped."""

    def test_hand_written_cast_not_double_wrapped(self) -> None:
        """A hand-written CAST(TO_JSON(x) AS JSON) is left as-is (no double wrap)."""
        sql = "SELECT CAST(TO_JSON([1, 2, 3]) AS JSON)"
        out = rewrite_json_helpers(sql)
        # The rewriter should not produce a double CAST.
        upper = out.upper()
        # Only one CAST(...) for the AS JSON wrap.
        assert upper.count("CAST(") == 1


class TestToJsonStringUnchanged:
    """``TO_JSON_STRING`` (to_json=False) is left alone."""

    def test_to_json_string_not_wrapped(self) -> None:
        """TO_JSON_STRING is not wrapped in CAST AS JSON."""
        sql = "SELECT TO_JSON_STRING([1, 2, 3])"
        out = rewrite_json_helpers(sql)
        # No CAST AS JSON wrap added.
        assert "AS JSON" not in out.upper()
