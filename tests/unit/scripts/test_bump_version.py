"""Unit tests for the version-bump tool (P4.c).

Pins six contracts the release orchestrator relies on:

1. **Parsing strictness** — only canonical ``X.Y.Z`` is accepted;
   pre-release / build-metadata suffixes are rejected.
2. **Field-wise comparison** — version ordering is lexicographic on
   the integer triple, not string compare.
3. **Bump semantics** — ``--major`` zeros minor+patch; ``--minor``
   zeros patch; ``--patch`` increments patch only.
4. **Mutual exclusion** — exactly one of an explicit version or a
   bump flag must be supplied.
5. **Strictly-greater invariant** — release pipeline refuses a flat
   or backward version jump (exit code 3).
6. **File round-trip preservation** — the substitution touches only
   the captured version triple; surrounding bytes (quoting, imports,
   docstring) round-trip byte-for-byte.

The script under test lives at
[`scripts/bump_version.py`](../../../scripts/bump_version.py) and is
called by [`scripts/release.py`](../../../scripts/release.py) during
the P5 release flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import bump_version as bump

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_INIT = '''"""bqemulator package."""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
'''

_DEFAULT_README = """# bqemulator

[![PyPI](https://img.shields.io/pypi/v/bqemulator.svg?cacheSeconds=120&v=0.1.0)](https://pypi.org/project/bqemulator/)
[![Python](https://img.shields.io/pypi/pyversions/bqemulator.svg?cacheSeconds=120&v=0.1.0)](https://pypi.org/project/bqemulator/)
"""


def _seed_init(tmp_path: Path, *, version: str = "0.1.0") -> Path:
    """Write a minimal ``__init__.py`` containing the supplied version."""
    init = tmp_path / "__init__.py"
    init.write_text(_DEFAULT_INIT.replace("0.1.0", version), encoding="utf-8")
    return init


def _seed_readme(tmp_path: Path, *, version: str = "0.1.0") -> Path:
    """Write a minimal ``README.md`` whose badge cache-bust matches ``version``."""
    readme = tmp_path / "README.md"
    readme.write_text(_DEFAULT_README.replace("0.1.0", version), encoding="utf-8")
    return readme


def _absent_readme(tmp_path: Path) -> Path:
    """Return a path to a non-existent README — used to skip the badge step."""
    return tmp_path / "no-readme.md"


# ---------------------------------------------------------------------------
# Version parsing + comparison
# ---------------------------------------------------------------------------


class TestParseVersion:
    """``parse_version`` accepts canonical X.Y.Z only."""

    def test_canonical_passes(self) -> None:
        v = bump.parse_version("1.2.3")
        assert v == bump.Version(1, 2, 3)

    @pytest.mark.parametrize(
        "raw",
        [
            "1.2",
            "1.2.3.4",
            "1.2.3-rc1",  # pre-release suffix rejected
            "1.2.3+build4",  # build-metadata suffix rejected
            "v1.2.3",  # leading 'v' rejected — the tag is built separately
            "1.2.3 ",  # trailing whitespace rejected
            "",  # empty
            "abc",  # garbage
        ],
    )
    def test_rejects_malformed(self, raw: str) -> None:
        with pytest.raises(bump.VersionFormatError):
            bump.parse_version(raw)


class TestVersionOrdering:
    """Version ordering is field-wise on the integer triple, not string compare."""

    def test_minor_dominates_string(self) -> None:
        # String compare puts "0.10.0" before "0.2.0" — the dataclass
        # ordering must put it after.
        assert bump.Version(0, 2, 0) < bump.Version(0, 10, 0)

    def test_patch_dominates_string(self) -> None:
        assert bump.Version(1, 0, 9) < bump.Version(1, 0, 10)

    def test_major_wins_against_minor(self) -> None:
        assert bump.Version(0, 99, 0) < bump.Version(1, 0, 0)

    def test_equal_is_not_greater(self) -> None:
        assert not bump.Version(1, 2, 3) > bump.Version(1, 2, 3)


# ---------------------------------------------------------------------------
# Bump semantics
# ---------------------------------------------------------------------------


class TestBumped:
    """``Version.bumped`` matches semver-roll conventions."""

    def test_major_zeros_lower(self) -> None:
        assert bump.Version(0, 9, 7).bumped("major") == bump.Version(1, 0, 0)

    def test_minor_zeros_patch(self) -> None:
        assert bump.Version(1, 2, 9).bumped("minor") == bump.Version(1, 3, 0)

    def test_patch_only_increments_patch(self) -> None:
        assert bump.Version(1, 2, 3).bumped("patch") == bump.Version(1, 2, 4)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown bump kind"):
            bump.Version(1, 0, 0).bumped("megapatch")


# ---------------------------------------------------------------------------
# resolve_target — mutual exclusion + strictly-greater invariant
# ---------------------------------------------------------------------------


class TestResolveTarget:
    """``resolve_target`` enforces mutual exclusion + strictly-greater."""

    def test_explicit_wins(self) -> None:
        target = bump.resolve_target(bump.Version(0, 1, 0), explicit="1.0.0", bump_kind=None)
        assert target == bump.Version(1, 0, 0)

    def test_bump_kind_wins(self) -> None:
        target = bump.resolve_target(bump.Version(0, 1, 0), explicit=None, bump_kind="minor")
        assert target == bump.Version(0, 2, 0)

    def test_neither_supplied_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            bump.resolve_target(bump.Version(0, 1, 0), explicit=None, bump_kind=None)

    def test_both_supplied_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            bump.resolve_target(bump.Version(0, 1, 0), explicit="1.0.0", bump_kind="major")

    def test_backward_jump_rejected(self) -> None:
        with pytest.raises(bump.VersionNotGreaterError):
            bump.resolve_target(bump.Version(0, 2, 0), explicit="0.1.9", bump_kind=None)

    def test_flat_jump_rejected(self) -> None:
        with pytest.raises(bump.VersionNotGreaterError):
            bump.resolve_target(bump.Version(0, 1, 0), explicit="0.1.0", bump_kind=None)


# ---------------------------------------------------------------------------
# File round-trip
# ---------------------------------------------------------------------------


class TestReadWrite:
    """Read+write preserve every byte outside the version triple."""

    def test_round_trip_preserves_surrounding_bytes(self, tmp_path: Path) -> None:
        init = _seed_init(tmp_path, version="0.1.0")
        original = init.read_text(encoding="utf-8")
        old = bump.write_new(bump.Version(0, 2, 0), init)
        assert old == bump.Version(0, 1, 0)
        updated = init.read_text(encoding="utf-8")
        # Only the version literal should differ.
        assert original.replace('"0.1.0"', '"0.2.0"') == updated

    def test_read_current_matches_what_write_new_writes(self, tmp_path: Path) -> None:
        init = _seed_init(tmp_path, version="3.14.159")
        assert bump.read_current(init) == bump.Version(3, 14, 159)

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            bump.read_current(tmp_path / "absent.py")

    def test_missing_version_line_raises(self, tmp_path: Path) -> None:
        init = tmp_path / "__init__.py"
        init.write_text("# no version here\n", encoding="utf-8")
        with pytest.raises(bump.VersionFormatError):
            bump.read_current(init)

    def test_single_quoted_version_round_trips(self, tmp_path: Path) -> None:
        # The source-of-truth file uses double quotes today, but the
        # regex accepts single-quoted form too — proven here so the
        # script doesn't silently corrupt a hand-edited single-quoted
        # alternative.
        init = tmp_path / "__init__.py"
        init.write_text("__version__ = '0.1.0'\n", encoding="utf-8")
        bump.write_new(bump.Version(0, 2, 0), init)
        assert init.read_text(encoding="utf-8") == "__version__ = '0.2.0'\n"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    """``main`` honours the documented exit-code contract."""

    def test_print_returns_current(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        init = _seed_init(tmp_path, version="1.2.3")
        rc = bump.main(["--print", "--file", str(init), "--readme", str(_absent_readme(tmp_path))])
        assert rc == bump.EXIT_OK
        captured = capsys.readouterr()
        assert captured.out.strip() == "1.2.3"
        # --print is read-only.
        assert init.read_text(encoding="utf-8").count("1.2.3") == 1

    def test_check_does_not_write(self, tmp_path: Path) -> None:
        init = _seed_init(tmp_path, version="0.1.0")
        rc = bump.main(
            [
                "--next",
                "minor",
                "--check",
                "--file",
                str(init),
                "--readme",
                str(_absent_readme(tmp_path)),
            ],
        )
        assert rc == bump.EXIT_OK
        assert "0.1.0" in init.read_text(encoding="utf-8")

    def test_apply_writes_new_version(self, tmp_path: Path) -> None:
        init = _seed_init(tmp_path, version="0.1.0")
        rc = bump.main(
            ["1.0.0", "--file", str(init), "--readme", str(_absent_readme(tmp_path))],
        )
        assert rc == bump.EXIT_OK
        assert '"1.0.0"' in init.read_text(encoding="utf-8")

    def test_not_greater_returns_dedicated_exit_code(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        init = _seed_init(tmp_path, version="0.5.0")
        rc = bump.main(
            ["0.1.0", "--file", str(init), "--readme", str(_absent_readme(tmp_path))],
        )
        assert rc == bump.EXIT_NOT_GREATER
        captured = capsys.readouterr()
        assert "not strictly greater" in captured.err

    def test_usage_error_on_malformed_version(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        init = _seed_init(tmp_path, version="0.1.0")
        rc = bump.main(
            ["1.2.3-rc1", "--file", str(init), "--readme", str(_absent_readme(tmp_path))],
        )
        assert rc == bump.EXIT_USAGE
        captured = capsys.readouterr()
        assert "canonical X.Y.Z" in captured.err

    def test_missing_args_returns_usage_error(self, tmp_path: Path) -> None:
        init = _seed_init(tmp_path, version="0.1.0")
        rc = bump.main(["--file", str(init), "--readme", str(_absent_readme(tmp_path))])
        assert rc == bump.EXIT_USAGE


# ---------------------------------------------------------------------------
# README badge cache-bust
# ---------------------------------------------------------------------------


class TestUpdateReadmeText:
    """Pure-function contract of the badge regex substitution.

    These tests pin behaviour callers depend on: the regex rewrites every
    matched badge in one pass; an unmatched README is returned unchanged
    with a ``0`` count; an already-bumped README is a textual no-op.
    """

    def test_both_badges_bumped_in_single_pass(self) -> None:
        text = (
            "[![PyPI](https://img.shields.io/pypi/v/bqemulator.svg"
            "?cacheSeconds=120&v=1.0.1)](pypi)\n"
            "[![Python](https://img.shields.io/pypi/pyversions/bqemulator.svg"
            "?cacheSeconds=120&v=1.0.1)](pypi)\n"
        )
        updated, count = bump.update_readme_text(text, bump.Version(1, 0, 2))
        assert count == 2
        assert "v=1.0.1" not in updated
        assert updated.count("v=1.0.2") == 2

    def test_missing_pattern_is_no_op(self) -> None:
        text = "# bqemulator\n\nNo badges here.\n"
        updated, count = bump.update_readme_text(text, bump.Version(1, 0, 2))
        assert count == 0
        assert updated == text

    def test_idempotent_substitution_byte_for_byte(self) -> None:
        text = (
            "[![PyPI](https://img.shields.io/pypi/v/bqemulator.svg"
            "?cacheSeconds=120&v=1.0.2)](pypi)\n"
        )
        updated, count = bump.update_readme_text(text, bump.Version(1, 0, 2))
        # Substitution still runs (count == 1), but result equals input.
        assert count == 1
        assert updated == text

    def test_alternate_cache_seconds_value_still_matches(self) -> None:
        # The regex is on ``cacheSeconds=\d+`` not the literal ``120`` —
        # a future tuning of the camo TTL must not silently de-match.
        text = "?cacheSeconds=900&v=0.9.5"
        updated, count = bump.update_readme_text(text, bump.Version(1, 0, 0))
        assert count == 1
        assert updated == "?cacheSeconds=900&v=1.0.0"


class TestWriteReadmeBadges:
    """File-IO wrapper handles the messy real-world inputs cleanly."""

    def test_writes_when_badges_change(self, tmp_path: Path) -> None:
        readme = _seed_readme(tmp_path, version="1.0.1")
        count = bump.write_readme_badges(bump.Version(1, 0, 2), readme)
        assert count == 2
        body = readme.read_text(encoding="utf-8")
        assert body.count("v=1.0.2") == 2
        assert "v=1.0.1" not in body

    def test_idempotent_skips_write_when_already_at_target(
        self,
        tmp_path: Path,
    ) -> None:
        readme = _seed_readme(tmp_path, version="1.0.2")
        mtime_before = readme.stat().st_mtime_ns
        count = bump.write_readme_badges(bump.Version(1, 0, 2), readme)
        # Contract: return value counts WRITTEN badges, not regex matches.
        # The README was already at 1.0.2 → regex matches both badges but
        # the substituted bytes equal the originals → no write → 0. This
        # is what suppresses the misleading "Updated N README badge(s)"
        # message in main() on idempotent re-runs.
        assert count == 0
        # No write happened — mtime unchanged.
        assert readme.stat().st_mtime_ns == mtime_before

    def test_missing_pattern_is_silent_no_op(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("# Nothing to see here\n", encoding="utf-8")
        mtime_before = readme.stat().st_mtime_ns
        count = bump.write_readme_badges(bump.Version(1, 0, 2), readme)
        assert count == 0
        assert readme.stat().st_mtime_ns == mtime_before

    def test_absent_file_returns_zero_no_raise(self, tmp_path: Path) -> None:
        # Tolerant of a renamed/missing README — the version bump must
        # not fail just because the badges moved out of the README.
        count = bump.write_readme_badges(bump.Version(1, 0, 2), tmp_path / "absent.md")
        assert count == 0

    def test_cli_apply_bumps_init_and_readme_together(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # End-to-end through ``main`` — the contract released
        # ``release.py`` callers depend on: one CLI call, both files
        # updated.
        init = _seed_init(tmp_path, version="1.0.1")
        readme = _seed_readme(tmp_path, version="1.0.1")
        rc = bump.main(["1.0.2", "--file", str(init), "--readme", str(readme)])
        assert rc == bump.EXIT_OK
        assert '"1.0.2"' in init.read_text(encoding="utf-8")
        assert "v=1.0.2" in readme.read_text(encoding="utf-8")
        captured = capsys.readouterr()
        assert "Bumped 1.0.1 -> 1.0.2" in captured.out
        assert "Updated 2 README badge(s)" in captured.out
