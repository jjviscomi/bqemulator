"""Unit tests for the recorder's error-shape payload helpers (P3.a).

The recorder's :func:`_build_message_pattern` converts a raw BigQuery
error message into a Python regex by:

1. Substituting the per-fixture dataset FQDN (both ``project.dataset``
   and the colon-separated ``project:dataset`` form BigQuery favours
   in error messages) for a regex matching any dataset-shaped token,
   so the same pattern survives re-recordings against a different
   project.
2. Replacing line:column markers (``[12:34]``) with a digit-range
   pattern — those drift trivially across recorder runs.
3. Regex-escaping every other character so the pattern matches the
   recorded literal text.

:func:`_build_error_payload` assembles the full ``expected.json``
envelope from a caught :class:`GoogleAPIError`. The tests pin both
helpers' contracts so a recorder regression fails fast.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit


def _load_recorder_module():
    """Load the recorder script as an importable module under a stable name.

    The recorder lives in ``scripts/record_conformance_fixtures.py`` —
    a path-not-a-package import in pytest's harness. Loading it via
    :mod:`importlib.util` gives the tests direct access to the
    private helpers without polluting the import graph elsewhere.
    """
    if "_recorder_under_test" not in globals():
        recorder_path = (
            Path(__file__).resolve().parents[3] / "scripts" / "record_conformance_fixtures.py"
        )
        spec = importlib.util.spec_from_file_location("_recorder_under_test", recorder_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()["_recorder_under_test"] = module
    return globals()["_recorder_under_test"]


@pytest.fixture(scope="module")
def recorder():
    """The loaded recorder module."""
    return _load_recorder_module()


class TestBuildMessagePattern:
    """The recorder's :func:`_build_message_pattern` regex synthesiser."""

    def test_literal_message_with_no_volatile_pieces_round_trips(self, recorder) -> None:
        """A message with no dataset / line:column markers escapes cleanly."""
        pattern = recorder._build_message_pattern(
            "Function CONCAT requires at least one argument", dataset_fqdn=None
        )
        compiled = re.compile(pattern)
        assert compiled.search("Function CONCAT requires at least one argument") is not None
        # Sibling messages do not match.
        assert compiled.search("Function SUBSTR requires at least two arguments") is None

    def test_dataset_fqdn_substituted_to_wildcard_dot_form(self, recorder) -> None:
        """``project.dataset`` in the raw message becomes a dataset-shaped wildcard."""
        message = "Not found: Table myproj.bqemu_temp_run42_t.t_does_not_exist"
        pattern = recorder._build_message_pattern(message, dataset_fqdn="myproj.bqemu_temp_run42_t")
        compiled = re.compile(pattern)
        # The same shape under a different project / dataset still matches.
        assert (
            compiled.search("Not found: Table other-proj.bqemu_emu_xyz.t_does_not_exist")
            is not None
        )

    def test_dataset_fqdn_substituted_to_wildcard_colon_form(self, recorder) -> None:
        """``project:dataset`` (BQ error favourite) also becomes a wildcard."""
        message = "Not found: Table myproj:bqemu_temp_run42_t.t_does_not_exist"
        pattern = recorder._build_message_pattern(message, dataset_fqdn="myproj.bqemu_temp_run42_t")
        compiled = re.compile(pattern)
        assert (
            compiled.search("Not found: Table emuproj:bqemu_emu_xyz.t_does_not_exist") is not None
        )

    def test_dataset_fqdn_substituted_with_dot_takes_precedence(self, recorder) -> None:
        """When both forms appear in the same message, both are normalised."""
        message = "Not found: Table myproj:ds_X.t and view myproj.ds_X.v"
        pattern = recorder._build_message_pattern(message, dataset_fqdn="myproj.ds_X")
        compiled = re.compile(pattern)
        assert (
            compiled.search("Not found: Table emuproj:ds_Y.t and view emuproj.ds_Y.v") is not None
        )

    def test_line_column_marker_substituted_to_digit_range(self, recorder) -> None:
        """``[12:34]`` markers normalise to ``\\[\\d+:\\d+\\]``."""
        message = "Syntax error: Unclosed parenthesis at [1:15]"
        pattern = recorder._build_message_pattern(message, dataset_fqdn=None)
        compiled = re.compile(pattern)
        # The line / column may drift in any reasonable way.
        assert compiled.search("Syntax error: Unclosed parenthesis at [1:15]") is not None
        assert compiled.search("Syntax error: Unclosed parenthesis at [3:42]") is not None
        # But the surrounding wording must be identical.
        assert compiled.search("Syntax error: Unexpected token at [3:42]") is None

    def test_regex_special_characters_in_message_are_escaped(self, recorder) -> None:
        """Backslashes / dots / parens in the message must not turn into regex ops."""
        message = "Bad value (a.b)? not parseable: \\reset"
        pattern = recorder._build_message_pattern(message, dataset_fqdn=None)
        compiled = re.compile(pattern)
        # The literal message matches.
        assert compiled.search("Bad value (a.b)? not parseable: \\reset") is not None
        # A close variant that exploits unescaped regex semantics does NOT match.
        assert compiled.search("Bad value (axb)? not parseable: \\reset") is None
        assert compiled.search("Bad value () not parseable: \\reset") is None

    def test_dataset_substitution_runs_before_escape(self, recorder) -> None:
        """The dataset wildcard is regex-active, not literal-escaped."""
        message = "Not found: Routine my-proj.my-ds.fn"
        pattern = recorder._build_message_pattern(message, dataset_fqdn="my-proj.my-ds")
        # The resulting pattern carries a character-class for the
        # dataset slot (not a literal substring).
        assert r"[\w\-\.:]+" in pattern

    def test_empty_message_yields_compilable_empty_pattern(self, recorder) -> None:
        """An empty raw message produces a compilable (zero-width) pattern."""
        pattern = recorder._build_message_pattern("", dataset_fqdn=None)
        compiled = re.compile(pattern)
        assert compiled.search("") is not None


class TestBuildErrorPayload:
    """The recorder's :func:`_build_error_payload` envelope assembler."""

    def _fake_exc(
        self,
        *,
        message: str,
        reason: str,
        location: str | None,
        http_status: int,
    ):
        """A fake BigQuery error in the canonical errors[] shape.

        We avoid constructing a real ``BadRequest`` here because some
        google-api-core versions infer ``code`` from the class rather
        than honouring an override; building a duck-typed shim is
        clearer and pinpoints exactly which attributes the recorder
        consumes.
        """
        from typing import ClassVar

        first_error: dict[str, str] = {"reason": reason, "message": message}
        if location is not None:
            first_error["location"] = location

        class _DuckError(Exception):
            errors: ClassVar[list[dict[str, str]]] = [first_error]
            code: ClassVar[int] = http_status

            def __init__(self) -> None:
                super().__init__(message)
                self.message = message

        return _DuckError()

    def test_envelope_carries_all_five_recorder_fields(self, recorder) -> None:
        """Recorded envelope carries fixture_version, bigquery, error, duration_class."""
        exc = self._fake_exc(
            message="Syntax error: Unclosed parenthesis at [1:15]",
            reason="invalidQuery",
            location="query",
            http_status=400,
        )

        class _FakeFixture:
            id = "standard_functions/error_syntax_unclosed_paren"

        payload = recorder._build_error_payload(
            fixture=_FakeFixture(),
            exc=exc,
            project="proj-1",
            actual_project="proj-1",
            location="US",
            dataset_fqdn="proj-1.bqemu_run42_t",
            wall_ms=42,
        )
        assert payload["fixture_version"] == 2
        assert payload["bigquery"]["project"] == "proj-1"
        assert payload["bigquery"]["location"] == "US"
        assert payload["bigquery"]["duration_ms"] == 42
        assert payload["error"]["reason"] == "invalidQuery"
        assert payload["error"]["location"] == "query"
        assert payload["error"]["http_status"] == 400
        assert "message_pattern" in payload["error"]
        assert payload["error"]["message_sample"] == "Syntax error: Unclosed parenthesis at [1:15]"
        # duration_class is present (the recorder's coarse-grade helper).
        assert payload["duration_class"] in {"fast", "medium", "slow"}

    def test_message_pattern_round_trips_through_extract_compare(self, recorder) -> None:
        """An error payload produced by the recorder diffs cleanly against the same exception."""
        from tests.conformance._comparison import compare_error, extract_actual_error

        exc = self._fake_exc(
            message="Not found: Table proj-1:bqemu_run42_t.tbl",
            reason="notFound",
            location="query",
            http_status=404,
        )

        class _FakeFixture:
            id = "rest_crud/error_table_not_found"

        payload = recorder._build_error_payload(
            fixture=_FakeFixture(),
            exc=exc,
            project="proj-1",
            actual_project="proj-1",
            location="US",
            dataset_fqdn="proj-1.bqemu_run42_t",
            wall_ms=10,
        )
        # The runner-side actual derived from the *same* exception
        # must compare ok against the recorder's expected payload.
        actual = extract_actual_error(exc)
        report = compare_error(payload["error"], actual)
        assert report.ok, report.reason

    def test_envelope_diffs_against_an_emulator_side_dataset_name(self, recorder) -> None:
        """A recorded envelope tolerates re-running with a different dataset name."""
        from tests.conformance._comparison import compare_error

        bq_exc = self._fake_exc(
            message="Not found: Table proj-bq:bqemu_run42_t.tbl",
            reason="notFound",
            location="query",
            http_status=404,
        )

        class _FakeFixture:
            id = "rest_crud/error_table_not_found"

        payload = recorder._build_error_payload(
            fixture=_FakeFixture(),
            exc=bq_exc,
            project="proj-bq",
            actual_project="proj-bq",
            location="US",
            dataset_fqdn="proj-bq.bqemu_run42_t",
            wall_ms=10,
        )
        # Emulator-side actual: same error but produced by a different
        # project + dataset. The message_pattern wildcard absorbs the
        # drift.
        emu_actual = {
            "reason": "notFound",
            "location": "query",
            "http_status": 404,
            "message": "Not found: Table proj-emu:bqemu_emu_xyz.tbl",
        }
        assert compare_error(payload["error"], emu_actual).ok

    def test_message_sample_scrubs_the_billing_project(self, recorder) -> None:
        """The real billing project never reaches the recorded envelope.

        BigQuery embeds the project in some error messages (e.g.
        ``Dataset <project>:<dataset> is still in use``). The
        human-readable ``message_sample`` must carry the
        ``your-bigquery-project`` placeholder, never the real id; the
        ``message_pattern`` already wildcards the dataset+project token.
        """
        exc = self._fake_exc(
            message="Dataset secret-proj-9:bqemu_run42_ds is still in use",
            reason="resourceInUse",
            location=None,
            http_status=400,
        )

        class _FakeFixture:
            id = "rest_crud/ddl_drop_schema_non_empty_restrict"

        payload = recorder._build_error_payload(
            fixture=_FakeFixture(),
            exc=exc,
            project=recorder.FIXTURE_PROJECT_PLACEHOLDER,
            actual_project="secret-proj-9",
            location="US",
            dataset_fqdn="secret-proj-9.bqemu_run42_ds",
            wall_ms=10,
        )
        error = payload["error"]
        assert "secret-proj-9" not in error["message_sample"]
        assert "secret-proj-9" not in error["message_pattern"]
        assert recorder.FIXTURE_PROJECT_PLACEHOLDER in error["message_sample"]
        assert error["reason"] == "resourceInUse"
        assert error["http_status"] == 400
        assert payload["bigquery"]["project"] == recorder.FIXTURE_PROJECT_PLACEHOLDER
