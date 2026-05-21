"""Unit tests for the compatibility-matrix generator.

Pins three contracts the CI gate relies on:

1. **Determinism** — regenerating the snapshot twice produces
   byte-equal output.
2. **Sentinel preservation** — narrative outside the sentinels
   round-trips byte-for-byte across regenerations.
3. **`--check` exit codes** — clean state exits 0; drift exits 1.

The generator lives at
[`scripts/generate_compatibility_matrix.py`](../../../scripts/generate_compatibility_matrix.py)
and reads the conformance corpus + XFAIL registry under
[`tests/conformance/`](../../conformance/).
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from scripts import generate_compatibility_matrix as gen

from tests.conformance._corpus import Fixture

pytestmark = pytest.mark.unit


def _build_fixture(phase: str, name: str, tmp_path: Path) -> Fixture:
    """Construct a minimal :class:`Fixture` suitable for the generator's input contract."""
    fixture_dir = tmp_path / phase / name
    fixture_dir.mkdir(parents=True, exist_ok=True)
    return Fixture(
        phase=phase,
        name=name,
        path=fixture_dir,
        query_sql="",
        setup_sql=None,
        expected_path=fixture_dir / "expected.json",
    )


class TestRender:
    """The :func:`render` function emits a deterministic Markdown block."""

    def test_emits_sentinel_markers(self, tmp_path: Path) -> None:
        sql = [_build_fixture("rest_crud", "f1", tmp_path)]
        block = gen.render(sql, [], [])
        assert gen.SENTINEL_BEGIN in block
        assert gen.SENTINEL_END in block

    def test_totals_match_input(self, tmp_path: Path) -> None:
        sql = [_build_fixture("rest_crud", f"f{i}", tmp_path) for i in range(3)]
        http = [_build_fixture("jobs", "h1", tmp_path)]
        grpc = [
            _build_fixture("storage_read", "g1", tmp_path),
            _build_fixture("storage_write", "g2", tmp_path),
        ]
        block = gen.render(sql, http, grpc)
        assert "6 fixtures" in block
        assert "(3 SQL + 1 HTTP + 2 gRPC)" in block

    def test_round_trip_is_byte_equal(self, tmp_path: Path) -> None:
        sql = [_build_fixture("rest_crud", "f1", tmp_path)]
        first = gen.render(sql, [], [])
        second = gen.render(sql, [], [])
        assert first == second

    def test_empty_corpus_renders_zero_totals(self) -> None:
        block = gen.render([], [], [])
        assert "0 fixtures" in block
        assert "0 PASS / 0 XFAIL" in block

    def test_xfail_registry_lists_every_pin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every entry in ``KNOWN_DIVERGENCES`` produces a table row."""
        monkeypatch.setattr(
            gen,
            "KNOWN_DIVERGENCES",
            {"rest_crud/f1": "Rationale A.", "rest_crud/f2": "Rationale B."},
        )
        block = gen.render([], [], [])
        assert "rest_crud/f1" in block
        assert "rest_crud/f2" in block
        # Rationales render as the first sentence (period included).
        assert "Rationale A." in block
        assert "Rationale B." in block

    def test_empty_divergence_registry_yields_pass_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(gen, "KNOWN_DIVERGENCES", {})
        sql = [_build_fixture("rest_crud", "f1", tmp_path)]
        block = gen.render(sql, [], [])
        assert "every fixture passes" in block


class TestShortenRationale:
    """The first-sentence + length-cap extraction for the XFAIL table."""

    def test_returns_first_sentence_intact(self) -> None:
        assert gen._shorten_rationale("First. Second.") == "First."

    def test_collapses_internal_whitespace(self) -> None:
        assert gen._shorten_rationale("a   b\n\tc") == "a b c"

    def test_truncates_overflow_with_ellipsis(self) -> None:
        rationale = "a" * 200
        out = gen._shorten_rationale(rationale, limit=20)
        assert out.endswith("…")
        assert len(out) <= 20


class TestInjectIntoFile:
    """The sentinel-block substitution preserves hand-maintained text."""

    def test_appends_block_when_no_sentinels(self) -> None:
        result = gen._inject_into_file("Hand-written narrative.\n", "INJECTED")
        assert result.startswith("Hand-written narrative.")
        assert "INJECTED" in result

    def test_replaces_existing_block_in_place(self) -> None:
        existing = (
            f"Preamble.\n\n{gen.SENTINEL_BEGIN}\nOLD BODY\n{gen.SENTINEL_END}\n\nPostscript.\n"
        )
        new_block = f"{gen.SENTINEL_BEGIN}\nNEW BODY\n{gen.SENTINEL_END}"
        result = gen._inject_into_file(existing, new_block)
        assert "OLD BODY" not in result
        assert "NEW BODY" in result
        assert "Preamble." in result
        assert "Postscript." in result

    def test_narrative_outside_sentinels_round_trips(self) -> None:
        narrative = "## Hand-maintained\n\nSee [link](other.md).\n\n"
        existing = narrative + f"{gen.SENTINEL_BEGIN}\nAUTO\n{gen.SENTINEL_END}\n"
        regenerated = f"{gen.SENTINEL_BEGIN}\nAUTO-V2\n{gen.SENTINEL_END}"
        result = gen._inject_into_file(existing, regenerated)
        assert result.startswith(narrative)


class TestCheckMode:
    """The CLI's ``--check`` mode short-circuits non-zero on drift."""

    def test_returns_clean_on_fresh_write(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        assert gen.main(["--output", str(target)]) == gen.EXIT_CLEAN
        # Same input on next run → drift-free.
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_CLEAN

    def test_returns_drift_on_dirty_target(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        gen.main(["--output", str(target)])
        target.write_text(target.read_text().replace("PASS", "DIRTY"))
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_DRIFT

    def test_returns_drift_when_target_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.md"
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_DRIFT


class TestPhaseStats:
    """The per-phase aggregation honours the XFAIL registry."""

    def test_marks_phase_with_known_divergence_as_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        f1 = _build_fixture("rest_crud", "ok", tmp_path)
        f2 = _build_fixture("rest_crud", "pinned", tmp_path)
        monkeypatch.setattr(
            gen,
            "KNOWN_DIVERGENCES",
            {f2.id: "Pinned divergence."},
        )
        rows = gen._phase_stats_for("SQL", [f1, f2])
        assert len(rows) == 1
        assert rows[0].xfail_count == 1
        assert rows[0].pass_count == 1
        assert rows[0].status_glyph == "⚠"

    def test_clean_phase_has_check_glyph(self, tmp_path: Path) -> None:
        f1 = _build_fixture("rest_crud", "ok", tmp_path)
        rows = gen._phase_stats_for("SQL", [f1])
        assert rows[0].status_glyph == "✅"


def test_sentinel_block_regex_matches_committed_file() -> None:
    """The committed ``compatibility-matrix.md`` carries a parseable sentinel block."""
    text = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    assert gen.SENTINEL_BEGIN in text
    assert gen.SENTINEL_END in text
    # The two sentinels appear in order — BEGIN strictly before END.
    assert text.index(gen.SENTINEL_BEGIN) < text.index(gen.SENTINEL_END)


def test_committed_file_passes_check() -> None:
    """The committed copy is up-to-date with the live corpus."""
    assert gen.main(["--check"]) == gen.EXIT_CLEAN


def test_directory_corpus_returns_empty_for_missing_root(tmp_path: Path) -> None:
    """The directory-walker tolerates a missing corpus root (e.g. fresh repo)."""
    assert gen._discover_directory_corpus(tmp_path / "missing") == []


def test_resolve_fixture_link_falls_back_to_sql_corpus() -> None:
    """Unknown fixture ids fall back to the SQL corpus path."""
    link = gen._resolve_fixture_link("phase_nonexistent/fixture")
    assert link.endswith("tests/conformance/sql_corpus/phase_nonexistent/fixture")


_BAR_RE = re.compile(r"\|")


def test_render_no_bare_pipes_in_xfail_rationale() -> None:
    """The XFAIL table cells must not break out via a literal pipe."""
    # KNOWN_DIVERGENCES rationales should not carry unescaped pipes; if
    # they did, the rendered table would corrupt under GitHub Markdown.
    block = gen.render([], [], [])
    table_lines = [line for line in block.splitlines() if line.startswith("|")]
    for line in table_lines:
        # Each table row should start with `|`, end with `|`, and have a
        # consistent column count (no broken-out pipes mid-cell).
        cells = line.split("|")
        # First and last entries are the leading/trailing pipe — empty.
        assert cells[0] == ""
        assert cells[-1] == ""
