"""Unit tests for the error-shape parity helpers (ADR 0022 §3, P3.a).

The P3.a framework extension adds two helpers to
:mod:`tests.conformance._comparison`:

* :func:`extract_actual_error` — normalises a :class:`GoogleAPIError`
  (or any other exception) to the four-field diff shape the
  conformance runner consumes.
* :func:`compare_error` — diffs the recorded ``error`` envelope
  against the normalised actual error.

This module exercises both helpers exhaustively so a future refactor
that changes either side of the contract fails fast.
"""

from __future__ import annotations

import pytest

from tests.conformance._comparison import (
    CompareReport,
    compare_error,
    extract_actual_error,
)

pytestmark = pytest.mark.unit


class TestExtractActualError:
    """:func:`extract_actual_error` extraction contract."""

    def test_bigquery_bad_request_with_errors_list(self) -> None:
        """A ``BadRequest`` with the canonical BQ ``errors[]`` shape extracts cleanly."""
        from google.api_core.exceptions import BadRequest

        exc = BadRequest(
            "Syntax error: Unclosed parenthesis at [1:15]",
            errors=[
                {
                    "reason": "invalidQuery",
                    "location": "query",
                    "message": "Syntax error: Unclosed parenthesis at [1:15]",
                }
            ],
        )
        actual = extract_actual_error(exc)
        assert actual == {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message": "Syntax error: Unclosed parenthesis at [1:15]",
        }

    def test_bigquery_not_found_404(self) -> None:
        """A ``NotFound`` raises the 404 / ``notFound`` shape."""
        from google.api_core.exceptions import NotFound

        exc = NotFound(
            "Not found: Table project:ds.t",
            errors=[
                {
                    "reason": "notFound",
                    "location": "query",
                    "message": "Not found: Table project:ds.t",
                }
            ],
        )
        actual = extract_actual_error(exc)
        assert actual["reason"] == "notFound"
        assert actual["http_status"] == 404
        assert actual["location"] == "query"

    def test_bigquery_conflict_409(self) -> None:
        """A ``Conflict`` raises the 409 / ``duplicate`` shape."""
        from google.api_core.exceptions import Conflict

        exc = Conflict(
            "Already Exists: Table project:ds.t",
            errors=[
                {
                    "reason": "duplicate",
                    "message": "Already Exists: Table project:ds.t",
                }
            ],
        )
        actual = extract_actual_error(exc)
        assert actual["reason"] == "duplicate"
        assert actual["http_status"] == 409
        # location absent in the structured payload → falls through to None.
        assert actual["location"] is None

    def test_no_errors_list_falls_back_to_message_attr(self) -> None:
        """Empty ``errors[]`` uses the top-level ``message`` attribute."""
        from typing import ClassVar

        from google.api_core.exceptions import GoogleAPICallError

        class _BareError(GoogleAPICallError):
            code: ClassVar[int] = 500

        exc = _BareError("upstream timed out")
        # No errors[] populated; message attr is what we have.
        actual = extract_actual_error(exc)
        assert actual["reason"] is None
        assert actual["location"] is None
        assert actual["http_status"] == 500
        assert actual["message"] == "upstream timed out"

    def test_first_error_dict_missing_keys_falls_through_to_none(self) -> None:
        """``errors[]`` with a dict missing ``reason``/``location`` leaves them ``None``."""
        from google.api_core.exceptions import BadRequest

        exc = BadRequest("bad", errors=[{"message": "bad"}])
        actual = extract_actual_error(exc)
        assert actual["reason"] is None
        assert actual["location"] is None
        assert actual["http_status"] == 400
        assert actual["message"] == "bad"

    def test_non_dict_first_error_treated_as_no_payload(self) -> None:
        """A non-dict in ``errors[0]`` falls through to the top-level message."""
        from google.api_core.exceptions import BadRequest

        # Strings in errors[] are degenerate but defensively handled.
        exc = BadRequest("the message", errors=["not a dict"])  # type: ignore[arg-type]
        actual = extract_actual_error(exc)
        assert actual["reason"] is None
        assert actual["location"] is None
        assert actual["http_status"] == 400
        # str(exc) is the fallback when the dict is unusable.
        assert "the message" in actual["message"]

    def test_non_api_exception_returns_str_only(self) -> None:
        """A bare :class:`Exception` populates only ``message`` from ``str(exc)``."""
        exc = ValueError("local emulator crashed")
        actual = extract_actual_error(exc)
        assert actual == {
            "reason": None,
            "location": None,
            "http_status": None,
            "message": "local emulator crashed",
        }


class TestCompareErrorHappyPath:
    """Equal envelopes compare clean."""

    def test_exact_match_all_fields(self) -> None:
        """A recorded envelope that matches actual fields end-to-end is ok."""
        expected = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message_pattern": r"Syntax error",
        }
        actual = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message": "Syntax error: Unclosed parenthesis at [1:15]",
        }
        report = compare_error(expected, actual)
        assert isinstance(report, CompareReport)
        assert report.ok
        assert report.diffs == []

    def test_message_pattern_regex_with_alternation(self) -> None:
        """The pattern is a real regex, not a literal substring."""
        expected = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message_pattern": r"(Unclosed|Unexpected) (paren|bracket)",
        }
        actual = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message": "Syntax: Unexpected bracket",
        }
        assert compare_error(expected, actual).ok

    def test_message_pattern_uses_search_not_fullmatch(self) -> None:
        """``re.search`` semantics: a pattern can match anywhere in the message."""
        expected = {
            "reason": "outOfRange",
            "location": None,
            "http_status": 400,
            "message_pattern": r"divisor is zero",
        }
        actual = {
            "reason": "outOfRange",
            "location": None,
            "http_status": 400,
            "message": "Error: query failed because divisor is zero — see docs",
        }
        assert compare_error(expected, actual).ok

    def test_message_pattern_dotall_for_multiline_messages(self) -> None:
        """``.`` matches newlines so multi-line BigQuery messages survive."""
        expected = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message_pattern": r"Syntax error.*Unclosed",
        }
        actual = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message": "Syntax error: line 1\nUnclosed parenthesis at [1:15]",
        }
        assert compare_error(expected, actual).ok


class TestCompareErrorMismatches:
    """Mismatched fields surface clean diffs."""

    def test_reason_mismatch(self) -> None:
        report = compare_error(
            {
                "reason": "invalidQuery",
                "location": None,
                "http_status": 400,
                "message_pattern": ".*",
            },
            {"reason": "notFound", "location": None, "http_status": 400, "message": "x"},
        )
        assert not report.ok
        assert "error.reason" in report.reason
        assert "'invalidQuery'" in report.reason
        assert "'notFound'" in report.reason

    def test_location_mismatch(self) -> None:
        report = compare_error(
            {"reason": "r", "location": "query", "http_status": 400, "message_pattern": ".*"},
            {"reason": "r", "location": "jobReference", "http_status": 400, "message": "x"},
        )
        assert not report.ok
        assert "error.location" in report.diffs[0]

    def test_http_status_mismatch(self) -> None:
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400, "message_pattern": ".*"},
            {"reason": "r", "location": None, "http_status": 404, "message": "x"},
        )
        assert not report.ok
        assert "error.http_status" in report.diffs[0]

    def test_message_pattern_no_match(self) -> None:
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400, "message_pattern": r"^Bad: foo$"},
            {"reason": "r", "location": None, "http_status": 400, "message": "Bad: bar"},
        )
        assert not report.ok
        assert "error.message" in report.diffs[0]
        assert "did not match" in report.diffs[0]

    def test_multiple_mismatches_accumulate(self) -> None:
        report = compare_error(
            {"reason": "r1", "location": "l1", "http_status": 400, "message_pattern": "x"},
            {"reason": "r2", "location": "l2", "http_status": 500, "message": "y"},
        )
        assert not report.ok
        assert len(report.diffs) == 4
        joined = "\n".join(report.diffs)
        assert "error.reason" in joined
        assert "error.location" in joined
        assert "error.http_status" in joined
        assert "error.message" in joined


class TestCompareErrorPatternEdgeCases:
    """Pathological ``message_pattern`` values do not crash the runner."""

    def test_missing_message_pattern_surfaces_clean_diff(self) -> None:
        """A recorded envelope without ``message_pattern`` reports the gap."""
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400},
            {"reason": "r", "location": None, "http_status": 400, "message": "x"},
        )
        assert not report.ok
        assert "message_pattern" in report.diffs[0]
        assert "missing" in report.diffs[0]

    def test_invalid_regex_surfaces_clean_diff(self) -> None:
        """An un-compilable pattern surfaces a clear failure, not a stack trace."""
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400, "message_pattern": r"["},
            {"reason": "r", "location": None, "http_status": 400, "message": "anything"},
        )
        assert not report.ok
        joined = "\n".join(report.diffs)
        assert "invalid regex" in joined

    def test_empty_pattern_matches_empty_message(self) -> None:
        """Empty pattern matches anything (``re.search`` allows zero-width match)."""
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400, "message_pattern": ""},
            {"reason": "r", "location": None, "http_status": 400, "message": ""},
        )
        assert report.ok

    def test_none_actual_message_is_treated_as_empty(self) -> None:
        """An actual without a populated message still routes through search."""
        report = compare_error(
            {"reason": "r", "location": None, "http_status": 400, "message_pattern": ""},
            {"reason": "r", "location": None, "http_status": 400, "message": None},
        )
        assert report.ok


class TestExtractToCompareRoundTrip:
    """End-to-end: extract from a raised exception, compare against expected."""

    def test_extracted_error_round_trips_through_compare(self) -> None:
        """An exception extracted via :func:`extract_actual_error` diffs cleanly."""
        from google.api_core.exceptions import BadRequest

        raised = BadRequest(
            "Syntax error: Unclosed parenthesis at [1:15]",
            errors=[
                {
                    "reason": "invalidQuery",
                    "location": "query",
                    "message": "Syntax error: Unclosed parenthesis at [1:15]",
                }
            ],
        )
        actual = extract_actual_error(raised)

        expected = {
            "reason": "invalidQuery",
            "location": "query",
            "http_status": 400,
            "message_pattern": r"Syntax error: Unclosed parenthesis at \[\d+:\d+\]",
        }
        report = compare_error(expected, actual)
        assert report.ok, report.reason
