"""Unit tests for the HTTP-shape conformance corpus framework (P2.f).

Pins the contract of:

- :mod:`tests.conformance._http_corpus` — fixture discovery, request /
  response models, JSON dotted-path walker, placeholder expansion.
- :mod:`tests.conformance._http_comparison` — structural-subset body
  comparison, WILDCARD semantics, header subset matching, recorder-side
  volatile-field masking.

The runner-side end-to-end behaviour is covered by the conformance
runner (``test_http_corpus.py``); this module pins the algorithms in
isolation so a regression in the dotted-path walker or the wildcard
matcher fails fast at the unit tier rather than as a flaky conformance
diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conformance._http_comparison import (
    HttpCompareReport,
    compare_http_response,
    mask_volatile_fields,
)
from tests.conformance._http_corpus import (
    WILDCARD,
    discover_http_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    resolve_dotted_path,
)

pytestmark = pytest.mark.unit


class TestResolveDottedPath:
    """Walks dicts and lists, fails loudly on a missing key."""

    def test_simple_key(self) -> None:
        assert resolve_dotted_path({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        body = {"jobReference": {"jobId": "abc"}}
        assert resolve_dotted_path(body, "jobReference.jobId") == "abc"

    def test_list_index(self) -> None:
        body = {"jobs": [{"id": "x"}, {"id": "y"}]}
        assert resolve_dotted_path(body, "jobs.1.id") == "y"

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError, match="not in"):
            resolve_dotted_path({"a": 1}, "b")

    def test_index_out_of_bounds_raises(self) -> None:
        with pytest.raises(IndexError, match="out of bounds"):
            resolve_dotted_path({"jobs": [{}]}, "jobs.5.id")

    def test_descend_into_scalar_raises(self) -> None:
        """Walking past a scalar should fail rather than silently yield None."""
        with pytest.raises(KeyError, match="cannot descend"):
            resolve_dotted_path({"a": "leaf"}, "a.b")


class TestExpandPlaceholders:
    """``${TOKEN}`` substitution must be strict and JSON-aware."""

    def test_string_substitution(self) -> None:
        assert expand_placeholders("hi ${NAME}", {"NAME": "world"}) == "hi world"

    def test_unknown_token_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown placeholder"):
            expand_placeholders("hi ${MISSING}", {"NAME": "world"})

    def test_json_walks_nested(self) -> None:
        value = {"path": "/p/${PROJECT}/q", "body": {"q": "SELECT ${N}"}}
        out = expand_placeholders_in_json(value, {"PROJECT": "p1", "N": "42"})
        assert out == {"path": "/p/p1/q", "body": {"q": "SELECT 42"}}

    def test_json_passes_scalars_through(self) -> None:
        """Numbers / booleans / None are untouched."""
        value = {"a": 1, "b": True, "c": None}
        assert expand_placeholders_in_json(value, {}) == value


class TestCompareHttpResponse:
    """Structural-subset body matching with WILDCARD semantics."""

    def _baseline(self) -> dict[str, object]:
        return {
            "expected_status": 200,
            "expected_body": {"kind": "x"},
            "expected_headers": (),
        }

    def test_exact_match(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={"kind": "x"},
            expected_headers=(),
            actual_status=200,
            actual_body={"kind": "x"},
            actual_headers={},
        )
        assert report.ok
        assert not report.diffs

    def test_status_mismatch(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={},
            expected_headers=(),
            actual_status=400,
            actual_body={},
            actual_headers={},
        )
        assert not report.ok
        assert any("http_status" in d for d in report.diffs)

    def test_wildcard_accepts_any_value(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={"jobReference": {"jobId": WILDCARD}},
            expected_headers=(),
            actual_status=200,
            actual_body={"jobReference": {"jobId": "anything-the-emulator-makes"}},
            actual_headers={},
        )
        assert report.ok

    def test_wildcard_accepts_absent_value(self) -> None:
        """A WILDCARD-valued key may be missing in the actual body."""
        report = compare_http_response(
            expected_status=200,
            expected_body={"jobReference": {"jobId": WILDCARD}},
            expected_headers=(),
            actual_status=200,
            actual_body={"jobReference": {}},  # no jobId at all
            actual_headers={},
        )
        assert report.ok

    def test_extra_keys_in_actual_are_tolerated(self) -> None:
        """Structural-subset: extras in actual are OK."""
        report = compare_http_response(
            expected_status=200,
            expected_body={"kind": "x"},
            expected_headers=(),
            actual_status=200,
            actual_body={"kind": "x", "newField": "bonus"},
            actual_headers={},
        )
        assert report.ok

    def test_missing_required_key_fails(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={"kind": "x", "required": 1},
            expected_headers=(),
            actual_status=200,
            actual_body={"kind": "x"},
            actual_headers={},
        )
        assert not report.ok
        assert any("required" in d and "absent" in d for d in report.diffs)

    def test_list_length_mismatch_fails(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={"rows": [{"v": 1}, {"v": 2}]},
            expected_headers=(),
            actual_status=200,
            actual_body={"rows": [{"v": 1}]},
            actual_headers={},
        )
        assert not report.ok
        assert any("list length mismatch" in d for d in report.diffs)

    def test_list_elementwise_diff(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={"rows": [{"v": "1"}, {"v": "2"}]},
            expected_headers=(),
            actual_status=200,
            actual_body={"rows": [{"v": "1"}, {"v": "WRONG"}]},
            actual_headers={},
        )
        assert not report.ok
        assert any("rows[1].v" in d for d in report.diffs)

    def test_headers_case_insensitive_subset_match(self) -> None:
        """Headers compare case-insensitively and only checked-keys are diffed."""
        report = compare_http_response(
            expected_status=200,
            expected_body={},
            expected_headers=(("Content-Type", "application/json"),),
            actual_status=200,
            actual_body={},
            actual_headers={
                "content-type": "application/json",
                "x-trace-id": "ignored",
            },
        )
        assert report.ok

    def test_header_value_mismatch(self) -> None:
        report = compare_http_response(
            expected_status=200,
            expected_body={},
            expected_headers=(("Content-Type", "application/json"),),
            actual_status=200,
            actual_body={},
            actual_headers={"content-type": "text/plain"},
        )
        assert not report.ok
        assert any("Content-Type" in d for d in report.diffs)

    def test_dict_actual_when_object_expected(self) -> None:
        """A non-object where an object is expected is reported clearly."""
        report = compare_http_response(
            expected_status=200,
            expected_body={"nested": {"k": "v"}},
            expected_headers=(),
            actual_status=200,
            actual_body={"nested": "string-not-object"},
            actual_headers={},
        )
        assert not report.ok
        assert any("expected object" in d for d in report.diffs)


class TestMaskVolatileFields:
    """The recorder's wildcard-masking helper."""

    def test_top_level_field(self) -> None:
        body = {"etag": "abc", "kind": "y"}
        mask_volatile_fields(body, ("etag",))
        assert body == {"etag": WILDCARD, "kind": "y"}

    def test_nested_field(self) -> None:
        body = {"jobReference": {"jobId": "abc", "location": "US"}}
        mask_volatile_fields(body, ("jobReference.jobId",))
        assert body == {"jobReference": {"jobId": WILDCARD, "location": "US"}}

    def test_list_each_mask(self) -> None:
        body = {"jobs": [{"id": "1"}, {"id": "2"}]}
        mask_volatile_fields(body, ("jobs[].id",))
        assert body == {"jobs": [{"id": WILDCARD}, {"id": WILDCARD}]}

    def test_missing_path_is_silent(self) -> None:
        """Masking a path that isn't present is a no-op."""
        body = {"kind": "x"}
        mask_volatile_fields(body, ("etag", "jobReference.jobId"))
        assert body == {"kind": "x"}

    def test_multiple_paths_in_one_call(self) -> None:
        body = {"a": 1, "b": {"c": 2}}
        mask_volatile_fields(body, ("a", "b.c"))
        assert body == {"a": WILDCARD, "b": {"c": WILDCARD}}


class TestDiscoverHttpFixtures:
    """End-to-end smoke test against the on-disk corpus."""

    def test_discovers_jobs_fixtures(self) -> None:
        fixtures = discover_http_fixtures()
        assert fixtures, "expected at least one HTTP fixture under http_corpus/"
        ids = {f.id for f in fixtures}
        # Spot-check the three categories required by the P2.f spec.
        assert any(fid.startswith("jobs/page_") for fid in ids)
        assert any(fid.startswith("jobs/job_") for fid in ids)
        assert any(fid.startswith("jobs/dryrun_") for fid in ids)

    def test_request_json_required(self, tmp_path: Path) -> None:
        """A fixture directory without a request.json is silently skipped."""
        phase = tmp_path / "jobs" / "no_request"
        phase.mkdir(parents=True)
        (phase / "setup.sql").write_text("SELECT 1")
        fixtures = discover_http_fixtures(corpus_dir=tmp_path, include_unrecorded=True)
        assert fixtures == []

    def test_expected_response_required_by_default(self, tmp_path: Path) -> None:
        """Discovery skips fixtures without expected_response.json unless asked."""
        phase = tmp_path / "jobs" / "no_expected"
        phase.mkdir(parents=True)
        (phase / "request.json").write_text('{"method": "GET", "path": "/x"}')
        # default: skipped
        assert discover_http_fixtures(corpus_dir=tmp_path) == []
        # include_unrecorded: surfaced
        fixtures = discover_http_fixtures(corpus_dir=tmp_path, include_unrecorded=True)
        assert len(fixtures) == 1
        assert fixtures[0].name == "no_expected"


class TestHttpCompareReportContract:
    """Type contract of the report dataclass."""

    def test_report_default_state(self) -> None:
        report = HttpCompareReport(ok=True)
        assert report.ok
        assert report.diffs == []

    def test_diffs_accumulate(self) -> None:
        """Multiple diffs are collected so an operator sees all regressions at once."""
        report = compare_http_response(
            expected_status=200,
            expected_body={"a": 1, "b": 2},
            expected_headers=(),
            actual_status=400,
            actual_body={"a": "wrong", "b": 2},
            actual_headers={},
        )
        assert len(report.diffs) >= 2  # status + body.a
