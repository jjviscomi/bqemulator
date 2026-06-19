"""Unit tests for the variation-taxonomy classifier + matrix generator (P8.a).

Pins the contract of :class:`tests.conformance._corpus.VariationTag`
and :func:`tests.conformance._corpus.classify_variation`, plus the
matrix-generator extensions in
[`scripts/generate_coverage_matrix.py`](../../../scripts/generate_coverage_matrix.py):
the per-surface variation histogram (rendered into the new
``Variation`` column on every per-category row) and the new
"Variation depth" report that enumerates broad-but-shallow surfaces.
The classifier contract is documented in
[ADR 0022](../../../docs/adr/0022-conformance-corpus-design.md)
§"Variation taxonomy" and the seven-tag set is locked there
(originally six; P8.e amended 2026-05-20 to add ``TIMEZONE``).

Tests use synthetic :class:`Fixture` instances rooted under
``tmp_path`` so the classifier exercises real on-disk content
(matching production: ``classify_variation`` reads ``expected.json``
for the ``error_path`` tag) without depending on the live corpus.
The byte-stability test calls into the generator end-to-end and
asserts that two successive renders against the same synthetic
inventory + corpus produce equal output (modulo the timestamp line).
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from scripts.generate_coverage_matrix import (
    _coverage,
    _format_fixture_links,
    _format_variation_histogram,
    _http_fixture_text,
    _render_variation_depth,
    _variation_histogram,
    _variation_tags,
    render,
)

from tests.conformance._corpus import (
    Fixture,
    VariationTag,
    classify_variation,
)
from tests.conformance._http_corpus import HttpFixture, HttpRequest
from tests.conformance._surface_inventory import SurfaceCategory, SurfaceItem, all_items

pytestmark = pytest.mark.unit


def _make_fixture(
    tmp_path: Path,
    *,
    name: str,
    query_sql: str,
    expected: dict[str, object] | None = None,
    phase: str = "standard_functions",
) -> Fixture:
    """Build a synthetic ``Fixture`` rooted at ``tmp_path``.

    Writes ``query.sql`` (always) and ``expected.json`` (when
    ``expected`` is non-None) into a per-fixture directory so the
    classifier sees real on-disk content and the ``error_path``
    detection actually opens a real file.
    """
    fx_dir = tmp_path / phase / name
    fx_dir.mkdir(parents=True, exist_ok=True)
    (fx_dir / "query.sql").write_text(query_sql, encoding="utf-8")
    expected_path = fx_dir / "expected.json"
    if expected is not None:
        expected_path.write_text(json.dumps(expected), encoding="utf-8")
    return Fixture(
        phase=phase,
        name=name,
        path=fx_dir,
        query_sql=query_sql,
        setup_sql=None,
        expected_path=expected_path,
    )


class TestVariationTagEnum:
    """The seven-tag set is locked. Any growth requires an ADR amendment."""

    def test_exactly_seven_tags(self) -> None:
        assert len(VariationTag) == 7

    def test_tag_values_are_lowercase_snake_case(self) -> None:
        """Tag values match the on-disk slug used in the matrix histogram."""
        assert {t.value for t in VariationTag} == {
            "happy_path",
            "null_input",
            "empty_input",
            "boundary_value",
            "unicode",
            "error_path",
            "timezone",
        }


class TestClassifyVariationHappyPath:
    """``HAPPY_PATH`` fires when no other tag matches and is mutually exclusive."""

    def test_plain_select_is_happy_path(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="select_one", query_sql="SELECT 1 AS n")
        assert classify_variation(fx) == frozenset({VariationTag.HAPPY_PATH})

    def test_happy_path_does_not_combine_with_other_tags(self, tmp_path: Path) -> None:
        """If any non-happy tag fires, ``HAPPY_PATH`` is absent from the result."""
        fx = _make_fixture(tmp_path, name="null_demo", query_sql="SELECT NULL AS n")
        tags = classify_variation(fx)
        assert VariationTag.NULL_INPUT in tags
        assert VariationTag.HAPPY_PATH not in tags


class TestClassifyVariationNullInput:
    """``NULL_INPUT`` fires on ``null`` substring in name OR ``NULL`` in SQL."""

    def test_null_keyword_in_name(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="null_upper", query_sql="SELECT UPPER(x) FROM t")
        assert VariationTag.NULL_INPUT in classify_variation(fx)

    def test_null_keyword_in_sql_projection(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="upper_basic", query_sql="SELECT UPPER(NULL) AS s")
        assert VariationTag.NULL_INPUT in classify_variation(fx)

    def test_is_null_predicate_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="filter_basic",
            query_sql="SELECT x FROM t WHERE x IS NULL",
        )
        assert VariationTag.NULL_INPUT in classify_variation(fx)

    def test_sql_without_null_does_not_fire(self, tmp_path: Path) -> None:
        """A fixture whose SQL has no NULL token + name without ``null`` is not tagged."""
        fx = _make_fixture(tmp_path, name="upper_basic", query_sql="SELECT UPPER('abc') AS s")
        assert VariationTag.NULL_INPUT not in classify_variation(fx)


class TestClassifyVariationEmptyInput:
    """``EMPTY_INPUT`` fires on ``empty`` name OR ``LIMIT 0`` / ``[]`` / ``''`` in SQL."""

    def test_empty_keyword_in_name(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path, name="empty_string_length", query_sql="SELECT LENGTH(s) FROM t"
        )
        assert VariationTag.EMPTY_INPUT in classify_variation(fx)

    def test_limit_zero_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="paged_one_row", query_sql="SELECT * FROM t LIMIT 0")
        assert VariationTag.EMPTY_INPUT in classify_variation(fx)

    def test_empty_array_literal_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="arr_basic",
            query_sql="SELECT ARRAY_LENGTH(CAST([] AS ARRAY<INT64>)) AS n",
        )
        assert VariationTag.EMPTY_INPUT in classify_variation(fx)

    def test_empty_string_literal_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="upper_demo", query_sql="SELECT UPPER('') AS s")
        assert VariationTag.EMPTY_INPUT in classify_variation(fx)

    def test_nonempty_string_does_not_fire(self, tmp_path: Path) -> None:
        """The ``''`` regex must match two adjacent single-quotes only."""
        fx = _make_fixture(tmp_path, name="upper_demo", query_sql="SELECT UPPER('abc') AS s")
        assert VariationTag.EMPTY_INPUT not in classify_variation(fx)


class TestClassifyVariationBoundaryValue:
    """``BOUNDARY_VALUE`` fires on boundary keywords in name OR extreme literals in SQL."""

    @pytest.mark.parametrize("token", ["max", "min", "inf", "nan"])
    def test_name_token_match(self, tmp_path: Path, token: str) -> None:
        fx = _make_fixture(
            tmp_path,
            name=f"bound_float64_{token}",
            query_sql="SELECT 1 AS n",
        )
        assert VariationTag.BOUNDARY_VALUE in classify_variation(fx)

    @pytest.mark.parametrize("keyword", ["boundary", "overflow"])
    def test_name_substring_match(self, tmp_path: Path, keyword: str) -> None:
        fx = _make_fixture(
            tmp_path,
            name=f"error_int64_{keyword}",
            query_sql="SELECT 1 AS n",
        )
        assert VariationTag.BOUNDARY_VALUE in classify_variation(fx)

    def test_long_integer_literal_in_sql(self, tmp_path: Path) -> None:
        """INT64 max is 19 digits; the regex catches anything >= 15 digits."""
        fx = _make_fixture(
            tmp_path,
            name="select_int_basic",
            query_sql="SELECT 9223372036854775807 AS n",
        )
        assert VariationTag.BOUNDARY_VALUE in classify_variation(fx)

    @pytest.mark.parametrize("literal", ["'Infinity'", "'NaN'", "'+inf'", "'-nan'"])
    def test_float_extreme_literal_in_sql(self, tmp_path: Path, literal: str) -> None:
        fx = _make_fixture(
            tmp_path,
            name="select_float",
            query_sql=f"SELECT CAST({literal} AS FLOAT64) AS x",
        )
        assert VariationTag.BOUNDARY_VALUE in classify_variation(fx)

    def test_does_not_fire_on_short_integer(self, tmp_path: Path) -> None:
        """A 14-digit integer literal is below the boundary-literal threshold."""
        fx = _make_fixture(
            tmp_path, name="select_int_basic", query_sql="SELECT 99999999999999 AS n"
        )
        assert VariationTag.BOUNDARY_VALUE not in classify_variation(fx)

    def test_does_not_fire_on_information_schema_inf_substring(self, tmp_path: Path) -> None:
        """``information`` must not match the ``inf`` token (snake-case tokenisation)."""
        fx = _make_fixture(
            tmp_path,
            name="caller_information_schema_visibility",
            query_sql="SELECT 1 AS n",
        )
        assert VariationTag.BOUNDARY_VALUE not in classify_variation(fx)


class TestClassifyVariationUnicode:
    """``UNICODE`` fires on ``unicode`` in name OR any non-ASCII codepoint in SQL."""

    def test_unicode_keyword_in_name(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path, name="unicode_combining_length", query_sql="SELECT LENGTH(s) FROM t"
        )
        assert VariationTag.UNICODE in classify_variation(fx)

    def test_non_ascii_codepoint_in_sql(self, tmp_path: Path) -> None:
        """A CJK character in a string literal triggers the tag."""
        fx = _make_fixture(
            tmp_path,
            name="length_demo",
            query_sql="SELECT LENGTH('日本語') AS n",
        )
        assert VariationTag.UNICODE in classify_variation(fx)

    def test_pure_ascii_sql_does_not_fire(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="upper_basic", query_sql="SELECT UPPER('abc') AS s")
        assert VariationTag.UNICODE not in classify_variation(fx)


class TestClassifyVariationErrorPath:
    """``ERROR_PATH`` fires when ``expected.json`` carries a top-level ``error`` key."""

    def test_fires_when_expected_has_error_envelope(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="bad_query",
            query_sql="SELECT FROM",
            expected={"error": {"reason": "invalidQuery", "http_status": 400}},
        )
        assert VariationTag.ERROR_PATH in classify_variation(fx)

    def test_does_not_fire_when_expected_lacks_error_key(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="good_query",
            query_sql="SELECT 1 AS n",
            expected={"schema": [], "rows": []},
        )
        assert VariationTag.ERROR_PATH not in classify_variation(fx)

    def test_missing_expected_file_does_not_crash(self, tmp_path: Path) -> None:
        """A fixture with a non-existent ``expected.json`` is not tagged ERROR_PATH."""
        fx = _make_fixture(tmp_path, name="bare", query_sql="SELECT 1 AS n")
        # File was not written because expected=None.
        assert not fx.expected_path.is_file()
        assert VariationTag.ERROR_PATH not in classify_variation(fx)


class TestClassifyVariationTimezone:
    """``TIMEZONE`` fires on ``tz_`` prefix in name OR timezone markers in SQL.

    P8.e (2026-05-20) added this tag once timezone arithmetic became its
    own variation-depth surface. The detection contract is:

    - The fixture name starts with ``tz_`` (the canonical prefix for the
      P8.e timezone-variation sweep).
    - OR ``query.sql`` contains ``AT TIME ZONE`` (case-insensitive, any
      whitespace).
    - OR ``query.sql`` carries an IANA-format zone literal such as
      ``'America/New_York'`` or ``'Etc/UTC'``.
    - OR ``query.sql`` carries a ``'+HH:MM'`` / ``'-HH:MM'`` offset literal
      (the second-argument form of ``DATETIME`` / ``TIMESTAMP`` / the
      ``%Ez`` format-string parsing path).
    """

    def test_tz_prefix_in_name(self, tmp_path: Path) -> None:
        fx = _make_fixture(tmp_path, name="tz_extract_hour_named_zone", query_sql="SELECT 1 AS n")
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_at_time_zone_operator_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="some_extract",
            query_sql=(
                "SELECT EXTRACT(HOUR FROM TIMESTAMP '2024-01-15 12:00:00+00' "
                "AT TIME ZONE 'America/New_York') AS h"
            ),
        )
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_named_zone_literal_in_sql(self, tmp_path: Path) -> None:
        """An IANA zone literal alone (no AT TIME ZONE) still fires."""
        fx = _make_fixture(
            tmp_path,
            name="trunc_demo",
            query_sql=(
                "SELECT TIMESTAMP_TRUNC(TIMESTAMP '2024-03-10 06:00:00+00', "
                "DAY, 'America/New_York') AS ts"
            ),
        )
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_etc_utc_synonym(self, tmp_path: Path) -> None:
        """``Etc/UTC`` is a deliberate match — exercises the named-zone surface."""
        fx = _make_fixture(
            tmp_path,
            name="utc_demo",
            query_sql=(
                "SELECT EXTRACT(HOUR FROM TIMESTAMP '2024-01-15 12:00:00+00' "
                "AT TIME ZONE 'Etc/UTC') AS h"
            ),
        )
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_positive_offset_literal_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="offset_demo",
            query_sql=(
                "SELECT TIMESTAMP_TRUNC(TIMESTAMP '2024-01-15 12:00:00+00', DAY, '+05:45') AS ts"
            ),
        )
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_negative_offset_literal_in_sql(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="offset_demo",
            query_sql=(
                "SELECT TIMESTAMP_TRUNC(TIMESTAMP '2024-01-15 12:00:00+00', DAY, '-04:30') AS ts"
            ),
        )
        assert VariationTag.TIMEZONE in classify_variation(fx)

    def test_plain_timestamp_no_zone_does_not_fire(self, tmp_path: Path) -> None:
        """A bare ``TIMESTAMP_ADD`` with no zone marker is not tagged TIMEZONE."""
        fx = _make_fixture(
            tmp_path,
            name="dt_timestamp_add_seconds",
            query_sql=(
                "SELECT TIMESTAMP_ADD(TIMESTAMP '2024-01-15 12:00:00+00', INTERVAL 60 SECOND) AS ts"
            ),
        )
        assert VariationTag.TIMEZONE not in classify_variation(fx)

    def test_dataset_path_does_not_false_positive(self, tmp_path: Path) -> None:
        """A backticked dataset path like ``foo.bar`` must not match the IANA pattern."""
        fx = _make_fixture(
            tmp_path,
            name="plain_select",
            query_sql="SELECT * FROM `myproject.mydataset.mytable`",
        )
        assert VariationTag.TIMEZONE not in classify_variation(fx)


class TestClassifyVariationMultiTag:
    """A single fixture can carry multiple non-happy tags simultaneously."""

    def test_null_and_unicode(self, tmp_path: Path) -> None:
        fx = _make_fixture(
            tmp_path,
            name="unicode_null_concat",
            query_sql="SELECT CONCAT('日本語', NULL) AS s",
        )
        tags = classify_variation(fx)
        assert tags == frozenset({VariationTag.NULL_INPUT, VariationTag.UNICODE})

    def test_boundary_and_error(self, tmp_path: Path) -> None:
        """The ``error_int64_overflow`` shape — boundary literal + error envelope."""
        fx = _make_fixture(
            tmp_path,
            name="error_int64_overflow",
            query_sql="SELECT 9223372036854775807 + 1 AS overflow",
            expected={"error": {"reason": "invalidQuery"}},
        )
        tags = classify_variation(fx)
        assert tags == frozenset({VariationTag.BOUNDARY_VALUE, VariationTag.ERROR_PATH})

    def test_timezone_and_error(self, tmp_path: Path) -> None:
        """The ``tz_error_unknown_zone`` shape — TIMEZONE + ERROR_PATH."""
        fx = _make_fixture(
            tmp_path,
            name="tz_error_unknown_zone",
            query_sql=(
                "SELECT EXTRACT(HOUR FROM TIMESTAMP '2024-01-15 12:00:00+00' "
                "AT TIME ZONE 'Mars/Olympus_Mons') AS h"
            ),
            expected={"error": {"reason": "invalidQuery"}},
        )
        tags = classify_variation(fx)
        assert tags == frozenset({VariationTag.TIMEZONE, VariationTag.ERROR_PATH})


class TestVariationHistogram:
    """The per-surface histogram aggregates tags across multiple fixtures."""

    def test_empty_fixture_list_returns_empty_dict(self) -> None:
        assert _variation_histogram([], {}) == {}

    def test_counts_tags_across_fixtures(self) -> None:
        variation_tags = {
            "f1": frozenset({VariationTag.HAPPY_PATH}),
            "f2": frozenset({VariationTag.HAPPY_PATH}),
            "f3": frozenset({VariationTag.NULL_INPUT, VariationTag.UNICODE}),
        }
        histogram = _variation_histogram(["f1", "f2", "f3"], variation_tags)
        assert histogram[VariationTag.HAPPY_PATH] == 2
        assert histogram[VariationTag.NULL_INPUT] == 1
        assert histogram[VariationTag.UNICODE] == 1
        assert histogram[VariationTag.EMPTY_INPUT] == 0

    def test_unknown_fixture_id_is_skipped(self) -> None:
        """A fixture id missing from the map contributes nothing — empty result."""
        assert _variation_histogram(["unknown"], {}) == {}


class TestFormatVariationHistogram:
    """The histogram renders as a compact ``happy×N / null×M`` string."""

    def test_empty_histogram_renders_dash(self) -> None:
        assert _format_variation_histogram({}) == "—"

    def test_all_zero_histogram_renders_dash(self) -> None:
        """A histogram with all-zero counts (no fixtures matched any tag) is dash."""
        histogram = dict.fromkeys(VariationTag, 0)
        assert _format_variation_histogram(histogram) == "—"

    def test_tag_order_follows_display_order(self) -> None:
        """Tags render in HAPPY → NULL → EMPTY → BOUND → UNICODE → ERROR order."""
        histogram = {
            VariationTag.ERROR_PATH: 1,
            VariationTag.HAPPY_PATH: 3,
            VariationTag.NULL_INPUT: 2,
        }
        rendered = _format_variation_histogram(histogram)
        assert rendered == "happy×3 / null×2 / error×1"


class TestVariationDepthReport:
    """The "Variation depth" report enumerates broad-but-shallow surfaces."""

    @pytest.fixture
    def hits_and_tags(
        self,
    ) -> tuple[dict[str, list[str]], dict[str, frozenset[VariationTag]]]:
        """A synthetic hits + tags table covering the three report-shape cases.

        - ``broad_shallow``: 5 fixtures, all happy_path → REPORTED
        - ``broad_varied``: 5 fixtures, mix of happy + null → NOT reported
        - ``narrow``: 2 fixtures, all happy_path → NOT reported (< threshold)
        - ``empty``: 0 fixtures → NOT reported
        """
        hits = {
            "broad_shallow": ["f1", "f2", "f3", "f4", "f5"],
            "broad_varied": ["g1", "g2", "g3", "g4", "g5"],
            "narrow": ["h1", "h2"],
            "empty": [],
        }
        variation_tags: dict[str, frozenset[VariationTag]] = {}
        for fid in ["f1", "f2", "f3", "f4", "f5"]:
            variation_tags[fid] = frozenset({VariationTag.HAPPY_PATH})
        for fid in ["g1", "g2", "g3"]:
            variation_tags[fid] = frozenset({VariationTag.HAPPY_PATH})
        for fid in ["g4", "g5"]:
            variation_tags[fid] = frozenset({VariationTag.NULL_INPUT})
        for fid in ["h1", "h2"]:
            variation_tags[fid] = frozenset({VariationTag.HAPPY_PATH})
        return hits, variation_tags

    def test_reports_broad_shallow_surface(
        self,
        hits_and_tags: tuple[dict[str, list[str]], dict[str, frozenset[VariationTag]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hits, variation_tags = hits_and_tags
        # Build a synthetic SURFACE so the report has something to walk.
        synthetic_surface = (
            SurfaceCategory(
                id="synth",
                name="Synthetic category",
                bq_docs="https://example.com",
                items=(
                    SurfaceItem(
                        id="broad_shallow",
                        name="BroadShallowItem",
                        bq_docs="https://example.com/bs",
                        detect=re.compile("X"),
                    ),
                    SurfaceItem(
                        id="broad_varied",
                        name="BroadVariedItem",
                        bq_docs="https://example.com/bv",
                        detect=re.compile("X"),
                    ),
                    SurfaceItem(
                        id="narrow",
                        name="NarrowItem",
                        bq_docs="https://example.com/n",
                        detect=re.compile("X"),
                    ),
                ),
            ),
        )
        monkeypatch.setattr("scripts.generate_coverage_matrix.SURFACE", synthetic_surface)
        rendered = _render_variation_depth(hits, variation_tags)
        # The broad-shallow surface IS listed.
        assert "BroadShallowItem" in rendered
        # The broad-varied surface is NOT listed (has non-happy tags).
        assert "BroadVariedItem" not in rendered
        # The narrow surface is NOT listed (below 3-fixture threshold).
        assert "NarrowItem" not in rendered

    def test_empty_report_shows_no_surfaces_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no broad-but-shallow surfaces, the report shows the empty marker."""
        synthetic_surface = (
            SurfaceCategory(
                id="synth",
                name="Synthetic",
                bq_docs="https://example.com",
                items=(
                    SurfaceItem(
                        id="varied",
                        name="VariedItem",
                        bq_docs="https://example.com/v",
                        detect=re.compile("X"),
                    ),
                ),
            ),
        )
        monkeypatch.setattr("scripts.generate_coverage_matrix.SURFACE", synthetic_surface)
        hits = {"varied": ["a", "b", "c"]}
        variation_tags = {
            "a": frozenset({VariationTag.NULL_INPUT}),
            "b": frozenset({VariationTag.HAPPY_PATH}),
            "c": frozenset({VariationTag.HAPPY_PATH}),
        }
        rendered = _render_variation_depth(hits, variation_tags)
        assert "_(no broad-but-shallow surfaces" in rendered

    def test_rows_sorted_by_fixture_count_descending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Broad-shallow surfaces appear sorted highest-fixture-count first."""
        synthetic_surface = (
            SurfaceCategory(
                id="synth",
                name="Synthetic",
                bq_docs="https://example.com",
                items=(
                    SurfaceItem(
                        id="small",
                        name="SmallItem",
                        bq_docs="https://example.com/s",
                        detect=re.compile("X"),
                    ),
                    SurfaceItem(
                        id="large",
                        name="LargeItem",
                        bq_docs="https://example.com/l",
                        detect=re.compile("X"),
                    ),
                ),
            ),
        )
        monkeypatch.setattr("scripts.generate_coverage_matrix.SURFACE", synthetic_surface)
        hits = {
            "small": ["a", "b", "c"],
            "large": ["d", "e", "f", "g", "h", "i", "j"],
        }
        variation_tags = {fid: frozenset({VariationTag.HAPPY_PATH}) for fid in "abcdefghij"}
        rendered = _render_variation_depth(hits, variation_tags)
        small_pos = rendered.find("SmallItem")
        large_pos = rendered.find("LargeItem")
        assert large_pos < small_pos, "Large surface should appear before small"


class TestVariationTagsAggregator:
    """``_variation_tags`` runs the classifier once per fixture."""

    def test_returns_dict_keyed_by_fixture_id(self, tmp_path: Path) -> None:
        f1 = _make_fixture(tmp_path, name="select_one", query_sql="SELECT 1 AS n")
        f2 = _make_fixture(tmp_path, name="null_demo", query_sql="SELECT NULL AS n")
        out = _variation_tags([f1, f2])
        assert out[f1.id] == frozenset({VariationTag.HAPPY_PATH})
        assert out[f2.id] == frozenset({VariationTag.NULL_INPUT})


class TestRenderByteStability:
    """Re-rendering the matrix against the same inputs is byte-stable (modulo timestamp)."""

    def test_two_renders_match_after_timestamp_strip(self, tmp_path: Path) -> None:
        """Generate, then regenerate; strip the timestamp line; assert equality."""
        f1 = _make_fixture(tmp_path, name="select_one", query_sql="SELECT 1 AS n")
        f2 = _make_fixture(tmp_path, name="null_demo", query_sql="SELECT NULL AS n")
        fixtures = [f1, f2]
        hits = {"surface.select": [f1.id, f2.id]}
        variation_tags = _variation_tags(fixtures)
        first = render(hits, fixtures, variation_tags)
        second = render(hits, fixtures, variation_tags)
        assert first == second


class TestHttpCorpusCoverage:
    """The coverage scan spans the HTTP corpus, not just sql_corpus."""

    def _http_fixture(self, tmp_path: Path, body: bytes) -> HttpFixture:
        fixture_dir = tmp_path / "jobs" / "fx"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "request.body.bin").write_bytes(body)
        return HttpFixture(
            phase="jobs",
            name="fx",
            path=fixture_dir,
            setup_sql=None,
            setup_requests=(
                HttpRequest(
                    method="POST",
                    path="/upload/bigquery/v2/projects/p/jobs",
                    body_bin="request.body.bin",
                ),
            ),
            request=HttpRequest(method="GET", path="/bigquery/v2/projects/p/datasets/d/tables/tbl"),
            expected_path=fixture_dir / "expected_response.json",
        )

    def test_http_fixture_text_includes_body_bin_and_id(self, tmp_path: Path) -> None:
        fixture = self._http_fixture(tmp_path, b'{"configuration": {"load": {"autodetect": true}}}')
        text = _http_fixture_text(fixture)
        assert '"autodetect": true' in text  # body_bin payload is scanned
        assert "jobs/fx" in text  # the fixture id is scanned
        assert "/tables/tbl" in text  # the canonical request path is scanned

    def test_coverage_detects_autodetect_surface_in_http_text(self) -> None:
        item = next(i for i in all_items() if i.id == "jobs.load_autodetect")
        texts = {"jobs/upload_x": '{"load": {"autodetect": true}}'}
        hits = _coverage([item], texts)
        assert hits["jobs.load_autodetect"] == ["jobs/upload_x"]

    def test_format_fixture_links_routes_http_ids_to_http_corpus(self) -> None:
        rendered = _format_fixture_links(
            ["jobs/upload_x", "standard_functions/abs_basic"],
            http_ids=frozenset({"jobs/upload_x"}),
        )
        assert "http_corpus/jobs/upload_x" in rendered
        assert "sql_corpus/standard_functions/abs_basic" in rendered
