"""Unit tests for the WKT-shaped ``STRING`` tolerance rule.

ADR 0022 §3 was amended on 2026-05-17 (Bucket H closure) so a
``STRING``-typed cell whose value matches the WKT geometry-type
pattern routes through the same whitespace + capitalisation
normalisation the ``GEOGRAPHY`` rule already applies. The rationale
is in ADR 0023 §1.H closure note: DuckDB-spatial emits
``POINT (1 2)`` (space before the paren) where BigQuery emits
``POINT(1 2)``, and ``ST_AsText`` returns ``STRING`` on the wire so
the GEOGRAPHY normalisation never fired previously.

The tests below pin the new contract: a WKT-shaped STRING value
normalises, a plain STRING value does not, and the regex is anchored
tightly enough that a URL or a JSON blob containing the word
``POINT`` is not mistaken for WKT.
"""

from __future__ import annotations

import pytest

from tests.conformance._comparison import CompareReport, compare_results


def _envelope(value: str | None) -> dict[str, object]:
    """Wrap ``value`` in the recorded-expected envelope shape."""
    return {
        "schema": [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        "rows": [{"wkt": value}],
    }


class TestWktShapedStringNormalisation:
    """The Bucket H closure contract — WKT-shaped STRING values normalise."""

    @pytest.mark.parametrize(
        ("expected_value", "actual_value"),
        [
            # POINT — the canonical Bucket H shape.
            ("POINT(1 2)", "POINT (1 2)"),
            ("POINT(1.5 2.5)", "POINT (1.5 2.5)"),
            # LINESTRING / POLYGON / their multi- variants.
            ("LINESTRING(0 0, 1 1, 2 2)", "LINESTRING (0 0, 1 1, 2 2)"),
            (
                "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))",
                "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))",
            ),
            (
                "MULTIPOINT(1 1, 2 2)",
                "MULTIPOINT (1 1, 2 2)",
            ),
            (
                "MULTILINESTRING((0 0, 1 1), (2 2, 3 3))",
                "MULTILINESTRING ((0 0, 1 1), (2 2, 3 3))",
            ),
            (
                "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 0)))",
                "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 0)))",
            ),
            (
                "GEOMETRYCOLLECTION(POINT(1 1))",
                "GEOMETRYCOLLECTION (POINT (1 1))",
            ),
        ],
    )
    def test_wkt_shaped_string_matches_with_whitespace_drift(
        self, expected_value: str, actual_value: str
    ) -> None:
        """Pairs that differ only in WKT-style whitespace compare equal."""
        report = compare_results(
            _envelope(expected_value),
            [{"wkt": actual_value}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert isinstance(report, CompareReport)
        assert report.ok, report.reason

    def test_wkt_shaped_string_case_insensitive_keyword(self) -> None:
        """Lowercase keyword still triggers the WKT-shape rule."""
        report = compare_results(
            _envelope("point(1 2)"),
            [{"wkt": "POINT (1 2)"}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert report.ok, report.reason

    def test_wkt_shaped_string_actual_coordinate_drift_still_fails(self) -> None:
        """Coordinate-value differences still surface as a mismatch.

        The new rule only forgives whitespace + capitalisation drift —
        it must NOT mask genuine geometry differences (the
        spheroidal-vs-planar centroid case is the obvious one).
        """
        report = compare_results(
            _envelope("POINT(2.00000000000004 2.00040218892024)"),
            [{"wkt": "POINT (2 2)"}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok
        assert "expected" in report.reason


class TestNonWktStringsUnaffected:
    """Non-WKT STRING values still apply exact-equality."""

    @pytest.mark.parametrize(
        "value",
        [
            "hello world",
            "https://example.com/path",
            "POINTLESS",  # starts with POINT but no opening paren
            '{"type": "Point", "coordinates": [1, 2]}',  # GeoJSON, starts with {
            "  POINT example without paren",  # POINT then no paren
            "",  # empty string
            "MULTI POINT (1 1)",  # has space inside the keyword (not WKT)
        ],
    )
    def test_non_wkt_strings_unchanged(self, value: str) -> None:
        """A non-WKT STRING that equals itself passes; a different one fails."""
        # Identity comparison must still pass.
        report_same = compare_results(
            _envelope(value),
            [{"wkt": value}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert report_same.ok, report_same.reason

        # A drift in the string is reported.
        drifted = value + " (extra)"
        if value:
            report_drift = compare_results(
                _envelope(value),
                [{"wkt": drifted}],
                [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
            )
            assert not report_drift.ok

    def test_mixed_wkt_and_non_wkt_does_not_normalise(self) -> None:
        """One-sided WKT shape must not trigger normalisation.

        If only one side is WKT-shaped the comparison falls back to
        exact equality — masking the divergence would be unsafe.
        """
        report = compare_results(
            _envelope("POINT(1 2)"),
            [{"wkt": "not a WKT value"}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok


class TestWktShapedStringNullsAndRepeated:
    """NULL and REPEATED-mode interactions stay sane."""

    def test_null_string_value_is_unaffected_by_wkt_rule(self) -> None:
        """A NULL-vs-value mismatch still reports normally."""
        report = compare_results(
            _envelope(None),
            [{"wkt": "POINT (1 2)"}],
            [{"name": "wkt", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok
        assert "NULL mismatch" in report.reason

    def test_repeated_wkt_string_array_normalises_per_element(self) -> None:
        """A REPEATED STRING column normalises element-by-element."""
        envelope = {
            "schema": [{"name": "wkts", "type": "STRING", "mode": "REPEATED"}],
            "rows": [{"wkts": ["POINT(1 2)", "POINT(3 4)"]}],
        }
        report = compare_results(
            envelope,
            [{"wkts": ["POINT (1 2)", "POINT (3 4)"]}],
            [{"name": "wkts", "type": "STRING", "mode": "REPEATED"}],
        )
        assert report.ok, report.reason
