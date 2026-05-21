"""Unit tests for the JSON-shaped ``STRING`` tolerance rule.

ADR 0022 §3 was amended on 2026-05-17 (out-of-scope GeoJSON closure)
so a ``STRING``-typed cell whose value's stripped form opens with
``{`` or ``[`` parses through ``json.loads`` and compares with
Python's unordered ``==``. The rationale: DuckDB-spatial's
``ST_AsGeoJSON`` emits ``{"coordinates": [3.0, 4.0], "type": "Point"}``
where BigQuery emits ``{ "type": "Point", "coordinates": [3, 4] } ``
— semantically equivalent JSON objects with different key order,
``int`` vs ``float`` coordinates, and inter-token whitespace.

A genuinely-malformed JSON string (or a JSON value where the two
sides disagree on a semantic field) still surfaces as a mismatch
— the rule only forgives shape-level rearrangement, not content
divergence.
"""

from __future__ import annotations

import pytest

from tests.conformance._comparison import CompareReport, compare_results


def _envelope(value: str | None) -> dict[str, object]:
    """Wrap ``value`` in the recorded-expected envelope shape."""
    return {
        "schema": [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        "rows": [{"gj": value}],
    }


class TestJsonShapedStringNormalisation:
    """STRING values that parse as JSON compare semantically."""

    def test_geojson_point_with_key_order_and_float_drift(self) -> None:
        """The canonical ``ST_AsGeoJSON`` divergence compares equal.

        Expected (BigQuery): integer coords, ``type`` before
        ``coordinates``, spaces after each ``:`` / ``,`` and a
        trailing space inside the closing brace.

        Actual (DuckDB-spatial): float coords, ``coordinates`` before
        ``type``, compact serialisation.
        """
        report = compare_results(
            _envelope('{ "type": "Point", "coordinates": [3, 4] } '),
            [{"gj": '{"coordinates": [3.0, 4.0], "type": "Point"}'}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert isinstance(report, CompareReport)
        assert report.ok, report.reason

    @pytest.mark.parametrize(
        ("expected_value", "actual_value"),
        [
            # Identical JSON content, different whitespace.
            ('{"a": 1, "b": 2}', '{"a":1,"b":2}'),
            # Identical JSON, different key order.
            ('{"a": 1, "b": 2}', '{"b": 2, "a": 1}'),
            # Identical JSON, int vs float (Python ``==`` treats these as equal).
            ('{"x": 1}', '{"x": 1.0}'),
            # JSON arrays (open with ``[``) work too.
            ("[1, 2, 3]", "[1.0, 2.0, 3.0]"),
            ("[1, 2, 3]", "[ 1, 2 , 3 ]"),
            # Nested.
            ('{"a": [1, 2], "b": {"c": 3}}', '{"b": {"c": 3}, "a": [1.0, 2.0]}'),
            # Trailing whitespace BigQuery sometimes emits.
            ('{"k": "v"} ', '{"k": "v"}'),
        ],
    )
    def test_parse_equal_json_pairs(self, expected_value: str, actual_value: str) -> None:
        """Pairs whose JSON parse yields equal Python objects compare equal."""
        report = compare_results(
            _envelope(expected_value),
            [{"gj": actual_value}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert report.ok, report.reason

    def test_semantically_different_json_still_fails(self) -> None:
        """A genuine semantic divergence still surfaces as a mismatch."""
        report = compare_results(
            _envelope('{"x": 1}'),
            [{"gj": '{"x": 2}'}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok
        assert "json-shaped string mismatch" in report.reason

    def test_malformed_json_falls_back_to_exact_equality(self) -> None:
        """If either side fails to parse, exact equality applies.

        This means a value that opens with ``{`` but is not valid JSON
        will still match itself byte-for-byte, but a different
        malformed-JSON-shaped string will fail (as desired — we don't
        silently mask malformed-JSON divergence).
        """
        # Identical malformed JSON: passes via exact equality fallback.
        same = compare_results(
            _envelope("{this is not json"),
            [{"gj": "{this is not json"}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert same.ok, same.reason

        # Different malformed JSON-shaped strings: fails.
        diff = compare_results(
            _envelope("{malformed A"),
            [{"gj": "{malformed B"}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not diff.ok


class TestJsonShapedFloatTolerance:
    """Float values inside JSON-shaped STRINGS compare with ULP tolerance.

    Closes the 4 ``st_asgeojson_*`` XFAILs (P3.d follow-up, 2026-05-19):
    BigQuery's geodesic-midpoint interpolation produces FLOAT64
    coordinates with 1-2 ULP drift from the emulator's libm output.
    The native FLOAT64 column comparator already tolerates that drift
    via ``math.isclose(rel_tol=1e-12, abs_tol=1e-15)``; this test pins
    the same contract for floats inside JSON-shaped strings so a
    coordinate that differs in the last bit no longer fails the diff.
    """

    def test_geojson_coordinate_with_ulp_drift_passes(self) -> None:
        """1-ULP drift on a GeoJSON coordinate compares equal."""
        # BigQuery's recorded value vs the emulator's libm value differ
        # by 4.2e-15 — within ``rel_tol=1e-12`` and ``abs_tol=1e-15``.
        report = compare_results(
            _envelope(
                '{ "type": "LineString", "coordinates": [ [1.49988573656168, 1.5000570914792] ] } '
            ),
            [
                {
                    "gj": '{"type":"LineString","coordinates":'
                    "[[1.4998857365616758,1.500057091479197]]}"
                }
            ],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert report.ok, report.reason

    def test_geojson_coordinate_beyond_tolerance_fails(self) -> None:
        """A genuine 1e-6 difference still surfaces — only ULP drift is forgiven."""
        report = compare_results(
            _envelope('{"x": 1.0}'),
            [{"gj": '{"x": 1.000001}'}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok

    def test_int_vs_float_still_equal(self) -> None:
        """The existing int-vs-float-equivalence behaviour is preserved."""
        report = compare_results(
            _envelope('{"x": 3}'),
            [{"gj": '{"x": 3.0}'}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert report.ok, report.reason

    def test_nan_vs_nan_treated_equal(self) -> None:
        """JSON has no NaN literal; this guards the helper against
        future GeoJSON-with-NaN drift."""
        from tests.conformance._comparison import (
            _objects_equal_with_float_tolerance,
        )

        assert _objects_equal_with_float_tolerance(float("nan"), float("nan"))

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (True, 1),
            (False, 0),
            (1, True),
            (0, False),
        ],
    )
    def test_bool_int_distinguished(self, a: object, b: object) -> None:
        """``True`` and ``1`` must NOT compare equal even though Python treats them so.

        ``isinstance(True, int)`` is True in Python; the comparator
        guards against this so a real ``true`` vs ``1`` divergence
        surfaces (matters for JSON schemas where a bool field is
        semantically different from an int field).
        """
        from tests.conformance._comparison import (
            _objects_equal_with_float_tolerance,
        )

        assert not _objects_equal_with_float_tolerance(a, b)


class TestNonJsonStringsUnaffected:
    """STRING values not starting with ``{`` or ``[`` use exact equality."""

    @pytest.mark.parametrize(
        "value",
        [
            "hello world",
            "POINT(1 2)",  # WKT — handled by separate rule, not JSON-shape
            "https://example.com/path",
            "1234567890",  # numeric STRING — not JSON-shaped
            "  Some text",  # leading whitespace, then non-JSON
            "true",  # JSON boolean literal but doesn't start with { or [
            "null",
        ],
    )
    def test_non_json_strings_unchanged(self, value: str) -> None:
        """Identity comparison passes; drift fails."""
        same = compare_results(
            _envelope(value),
            [{"gj": value}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert same.ok, same.reason

        diff = compare_results(
            _envelope(value),
            [{"gj": value + " (drift)"}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not diff.ok

    def test_one_sided_json_shape_uses_exact_equality(self) -> None:
        """If only one side is JSON-shaped, fall through to exact equality.

        Masking a one-sided drift via JSON normalisation would be unsafe —
        a real divergence (one side dropped the JSON wrapper, say)
        should surface as a mismatch.
        """
        report = compare_results(
            _envelope('{"k": "v"}'),
            [{"gj": "not json"}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok


class TestJsonShapedStringEdgeCases:
    """NULL, REPEATED, and empty-string cases."""

    def test_null_value_unaffected(self) -> None:
        """A NULL-vs-JSON mismatch still reports normally."""
        report = compare_results(
            _envelope(None),
            [{"gj": '{"a": 1}'}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert not report.ok
        assert "NULL mismatch" in report.reason

    def test_empty_string_falls_through_to_exact_equality(self) -> None:
        """Empty string is not JSON-shaped — exact equality applies."""
        same = compare_results(
            _envelope(""),
            [{"gj": ""}],
            [{"name": "gj", "type": "STRING", "mode": "NULLABLE"}],
        )
        assert same.ok

    def test_repeated_json_array_normalises_per_element(self) -> None:
        """A REPEATED STRING column normalises element-by-element."""
        envelope = {
            "schema": [{"name": "gjs", "type": "STRING", "mode": "REPEATED"}],
            "rows": [{"gjs": ['{"a": 1}', '{"b": 2}']}],
        }
        report = compare_results(
            envelope,
            [{"gjs": ['{"a": 1.0}', '{"b": 2.0}']}],
            [{"name": "gjs", "type": "STRING", "mode": "REPEATED"}],
        )
        assert report.ok, report.reason
