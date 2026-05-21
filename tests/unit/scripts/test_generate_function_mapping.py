"""Unit tests for the function-mapping generator.

Pins three contracts the CI gate relies on:

1. **Determinism** — regenerating the registry twice produces
   byte-equal output.
2. **Sentinel preservation** — narrative outside the sentinels
   round-trips byte-for-byte across regenerations.
3. **`--check` exit codes** — clean state exits 0; drift exits 1.

The generator lives at
[`scripts/generate_function_mapping.py`](../../../scripts/generate_function_mapping.py)
and walks
[`src/bqemulator/sql/rules/`](../../../src/bqemulator/sql/rules/) +
[`src/bqemulator/sql/rewriter/`](../../../src/bqemulator/sql/rewriter/)
for its source-of-truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import generate_function_mapping as gen

pytestmark = pytest.mark.unit


class TestSplitDocstring:
    """The ``X → Y`` extraction handles every observed arrow form."""

    def test_unicode_arrow_splits_lhs_rhs(self) -> None:
        bq, ddb = gen._split_docstring("``FOO(x)`` → ``BAR(x)``.")
        assert bq == "`FOO(x)`"
        assert ddb == "`BAR(x)`"

    def test_ascii_arrow_splits_lhs_rhs(self) -> None:
        bq, ddb = gen._split_docstring("``FOO(x)`` -> ``BAR(x)``.")
        assert bq == "`FOO(x)`"
        assert ddb == "`BAR(x)`"

    def test_fat_arrow_splits_lhs_rhs(self) -> None:
        bq, ddb = gen._split_docstring("``A`` => ``B``.")
        assert bq == "`A`"
        assert ddb == "`B`"

    def test_arrow_absent_keeps_text_in_lhs(self) -> None:
        doc = "Pre-translate BigQuery SQL for ``COLLATE(value, specifier)``."
        bq, ddb = gen._split_docstring(doc)
        assert "COLLATE(value, specifier)" in bq
        assert ddb == ""

    def test_empty_docstring_returns_empty_pair(self) -> None:
        assert gen._split_docstring("") == ("", "")
        assert gen._split_docstring(None or "") == ("", "")

    def test_only_first_line_consumed(self) -> None:
        doc = "``FOO`` → ``BAR``.\n\nLong second paragraph."
        bq, ddb = gen._split_docstring(doc)
        assert bq == "`FOO`"
        assert ddb == "`BAR`"

    def test_rst_double_backticks_collapse_to_markdown_single(self) -> None:
        """Inline ``X`` segments don't leak stray backticks into the cell."""
        bq, ddb = gen._split_docstring("``A`` or ``B`` → ``C`` (plus ``D``).")
        assert "`A`" in bq
        assert "`B`" in bq
        assert "``" not in bq
        assert "``" not in ddb


class TestNormaliseDocstringFragment:
    """The RST → Markdown converter strips trailing periods."""

    def test_strips_trailing_period(self) -> None:
        assert gen._normalise_docstring_fragment("foo.") == "foo"

    def test_preserves_internal_period(self) -> None:
        assert gen._normalise_docstring_fragment("a.b.c") == "a.b.c"


class TestCollectRules:
    """The rule collector reaches every entry in the live registry."""

    def test_returns_one_entry_per_registered_rule(self) -> None:
        from bqemulator.sql.rules import get_all_rules

        collected = gen._collect_rules()
        assert len(collected) == len(get_all_rules())

    def test_every_rule_carries_a_category(self) -> None:
        for entry in gen._collect_rules():
            assert entry.category
            assert entry.kind == "rule"


class TestCollectRewriters:
    """The rewriter collector skips the INFORMATION_SCHEMA module."""

    def test_information_schema_is_skipped(self) -> None:
        modules = {entry.module for entry in gen._collect_rewriters()}
        assert "information_schema" not in modules

    def test_returns_at_least_one_rewriter(self) -> None:
        assert len(gen._collect_rewriters()) >= 1

    def test_every_entry_marked_as_rewriter(self) -> None:
        for entry in gen._collect_rewriters():
            assert entry.kind == "rewriter"


class TestRender:
    """The :func:`render` function emits a deterministic Markdown block."""

    def test_emits_sentinel_markers(self) -> None:
        block = gen.render(gen._collect_rules(), gen._collect_rewriters())
        assert gen.SENTINEL_BEGIN in block
        assert gen.SENTINEL_END in block

    def test_includes_registered_rule_count(self) -> None:
        rules = gen._collect_rules()
        block = gen.render(rules, [])
        assert f"Registered rules**: {len(rules)}" in block

    def test_round_trip_is_byte_equal(self) -> None:
        first = gen.render(gen._collect_rules(), gen._collect_rewriters())
        second = gen.render(gen._collect_rules(), gen._collect_rewriters())
        assert first == second

    def test_empty_inputs_render_placeholder(self) -> None:
        block = gen.render([], [])
        assert "no entries" in block


class TestInjectIntoFile:
    """The sentinel-block substitution preserves hand-maintained text."""

    def test_appends_block_when_no_sentinels(self) -> None:
        result = gen._inject_into_file("Hand-maintained intro.\n", "INJECTED")
        assert result.startswith("Hand-maintained intro.")
        assert "INJECTED" in result

    def test_replaces_existing_block_in_place(self) -> None:
        existing = f"Preamble.\n\n{gen.SENTINEL_BEGIN}\nOLD\n{gen.SENTINEL_END}\n\nPostscript.\n"
        new_block = f"{gen.SENTINEL_BEGIN}\nNEW\n{gen.SENTINEL_END}"
        result = gen._inject_into_file(existing, new_block)
        assert "OLD" not in result
        assert "NEW" in result
        assert "Preamble." in result
        assert "Postscript." in result

    def test_narrative_outside_sentinels_round_trips(self) -> None:
        narrative = "## Hand-maintained intro\n\nSee [contrib](other.md).\n\n"
        existing = narrative + f"{gen.SENTINEL_BEGIN}\nAUTO\n{gen.SENTINEL_END}\n"
        regenerated = f"{gen.SENTINEL_BEGIN}\nAUTO-V2\n{gen.SENTINEL_END}"
        result = gen._inject_into_file(existing, regenerated)
        assert result.startswith(narrative)


class TestCheckMode:
    """The CLI's ``--check`` mode short-circuits non-zero on drift."""

    def test_returns_clean_on_fresh_write(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        assert gen.main(["--output", str(target)]) == gen.EXIT_CLEAN
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_CLEAN

    def test_returns_drift_on_dirty_target(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        gen.main(["--output", str(target)])
        target.write_text(target.read_text().replace("Registered", "Dirty"))
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_DRIFT

    def test_returns_drift_when_target_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.md"
        assert gen.main(["--output", str(target), "--check"]) == gen.EXIT_DRIFT


def test_sentinel_block_regex_matches_committed_file() -> None:
    """The committed ``sql-function-mapping.md`` carries a parseable sentinel block."""
    text = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    assert gen.SENTINEL_BEGIN in text
    assert gen.SENTINEL_END in text
    assert text.index(gen.SENTINEL_BEGIN) < text.index(gen.SENTINEL_END)


def test_committed_file_passes_check() -> None:
    """The committed copy is up-to-date with the live registry."""
    assert gen.main(["--check"]) == gen.EXIT_CLEAN


def test_escape_cell_protects_pipe_character() -> None:
    """A docstring containing ``|`` cannot break out of a Markdown table cell."""
    assert gen._escape_cell("a | b") == "a \\| b"
    assert gen._escape_cell("plain") == "plain"
