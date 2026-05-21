"""Unit tests for the changelog-finalisation tool (P4.c).

Pins six contracts the release orchestrator relies on:

1. **Unreleased → versioned promotion** — the captured Unreleased body
   lands verbatim under ``## [X.Y.Z] — YYYY-MM-DD`` and a fresh empty
   Unreleased section replaces it.
2. **Header-line preservation** — every byte outside the Unreleased
   block (preamble, prior versioned sections) round-trips identically.
3. **Empty-section refusal** — by default, finalising an empty
   ``Unreleased`` exits non-zero; ``--allow-empty`` is the explicit
   escape hatch.
4. **Duplicate-version refusal** — finalising a version already
   present in the file is an error.
5. **Strict version + date formats** — only canonical ``X.Y.Z`` and
   ``YYYY-MM-DD`` pass validation.
6. **CLI exit-code contract** — each error class maps to a stable,
   distinct exit code so ``scripts/release.py`` can detect and report
   the specific failure.

The script under test lives at
[`scripts/changelog.py`](../../../scripts/changelog.py).
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from scripts import changelog as cl

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_PREAMBLE = """\
# Changelog

All notable changes to this project are documented in this file.

"""

_UNRELEASED_BODY = """\
### Added

- new feature A.

### Fixed

- fix B.
"""

_PRIOR = """\
## [0.1.0] — 2026-01-01

### Added

- initial release.
"""


def _seed_changelog(
    tmp_path: Path,
    *,
    unreleased: str = _UNRELEASED_BODY,
    has_prior: bool = True,
    has_unreleased: bool = True,
) -> Path:
    """Compose a minimal CHANGELOG.md with the supplied Unreleased body."""
    sections = [_PREAMBLE]
    if has_unreleased:
        sections.append("## [Unreleased]\n\n")
        if unreleased:
            sections.append(unreleased)
            if not unreleased.endswith("\n"):
                sections.append("\n")
            sections.append("\n")
    if has_prior:
        sections.append(_PRIOR)
    path = tmp_path / "CHANGELOG.md"
    path.write_text("".join(sections), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# finalize() — happy path
# ---------------------------------------------------------------------------


class TestFinalizeHappyPath:
    """The captured body moves under the new versioned section."""

    def test_body_preserved_under_new_section(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0", date="2026-05-21")
        assert "## [1.0.0] — 2026-05-21" in updated
        assert "- new feature A." in updated
        assert "- fix B." in updated

    def test_unreleased_is_emptied_after_finalize(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0", date="2026-05-21")
        # The Unreleased header survives — but its body is empty until the
        # next PR lands.
        unreleased_idx = updated.index("## [Unreleased]")
        version_idx = updated.index("## [1.0.0]")
        between = updated[unreleased_idx + len("## [Unreleased]") : version_idx]
        assert between.strip() == ""

    def test_preamble_round_trips_byte_for_byte(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0", date="2026-05-21")
        # The preamble (everything before ``## [Unreleased]``) must be
        # untouched.
        assert updated.startswith(_PREAMBLE + "## [Unreleased]")

    def test_prior_versioned_section_survives(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0", date="2026-05-21")
        assert "## [0.1.0] — 2026-01-01" in updated
        assert "- initial release." in updated

    def test_default_date_is_today_utc(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0")
        # _today_iso uses UTC; the date should appear in the rendered
        # header. We only assert the format, not the exact value.
        assert re.search(r"## \[1\.0\.0\] — \d{4}-\d{2}-\d{2}", updated)


# ---------------------------------------------------------------------------
# finalize() — error paths
# ---------------------------------------------------------------------------


class TestFinalizeErrorPaths:
    """``finalize`` rejects malformed or unsafe states cleanly."""

    def test_missing_unreleased_section_raises(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, has_unreleased=False)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(cl.NoUnreleasedSectionError):
            cl.finalize(text, version="1.0.0", date="2026-05-21")

    def test_empty_unreleased_rejected_by_default(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, unreleased="")
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(cl.EmptyUnreleasedSectionError):
            cl.finalize(text, version="1.0.0", date="2026-05-21")

    def test_empty_unreleased_allowed_with_flag(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, unreleased="")
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.finalize(text, version="1.0.0", date="2026-05-21", allow_empty=True)
        assert cl.PLACEHOLDER_BODY in updated

    def test_duplicate_version_rejected(self, tmp_path: Path) -> None:
        # Seed a changelog that already has the ``[1.0.0]`` section.
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8") + "\n## [1.0.0] — 2025-01-01\n"
        with pytest.raises(cl.DuplicateVersionError):
            cl.finalize(text, version="1.0.0", date="2026-05-21")

    @pytest.mark.parametrize(
        "bad_version",
        ["1.0", "1.0.0.0", "1.0.0-rc1", "v1.0.0", "abc"],
    )
    def test_malformed_version_raises_value_error(self, tmp_path: Path, bad_version: str) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match=r"canonical X\.Y\.Z"):
            cl.finalize(text, version=bad_version, date="2026-05-21")

    @pytest.mark.parametrize(
        "bad_date",
        ["2026/05/21", "2026-5-21", "21-05-2026", "today"],
    )
    def test_malformed_date_raises_value_error(self, tmp_path: Path, bad_date: str) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="ISO-8601"):
            cl.finalize(text, version="1.0.0", date=bad_date)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    """``main`` returns the documented exit codes."""

    def test_check_passes_and_does_not_write(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        before = cl_path.read_text(encoding="utf-8")
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--check", "--file", str(cl_path)])
        assert rc == cl.EXIT_OK
        assert cl_path.read_text(encoding="utf-8") == before

    def test_apply_writes_finalised_changelog(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_OK
        updated = cl_path.read_text(encoding="utf-8")
        assert "## [1.0.0] — 2026-05-21" in updated
        assert "- new feature A." in updated

    def test_no_unreleased_returns_exit_3(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, has_unreleased=False)
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_NO_UNRELEASED

    def test_empty_unreleased_returns_exit_4(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, unreleased="")
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_EMPTY_UNRELEASED

    def test_empty_unreleased_with_allow_empty_succeeds(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, unreleased="")
        rc = cl.main(
            [
                "1.0.0",
                "--date",
                "2026-05-21",
                "--allow-empty",
                "--file",
                str(cl_path),
            ]
        )
        assert rc == cl.EXIT_OK

    def test_duplicate_version_returns_exit_5(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        # Pre-pend a [1.0.0] section so the script's duplicate check fires.
        text = cl_path.read_text(encoding="utf-8")
        cl_path.write_text(text + "\n## [1.0.0] — 2025-01-01\n", encoding="utf-8")
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_DUPLICATE_VERSION

    def test_malformed_version_returns_exit_2(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        rc = cl.main(["1.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_USAGE

    def test_malformed_date_returns_exit_2(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        rc = cl.main(["1.0.0", "--date", "today", "--file", str(cl_path)])
        assert rc == cl.EXIT_USAGE

    def test_missing_file_returns_exit_2(self, tmp_path: Path) -> None:
        rc = cl.main(
            [
                "1.0.0",
                "--date",
                "2026-05-21",
                "--file",
                str(tmp_path / "nope.md"),
            ]
        )
        assert rc == cl.EXIT_USAGE


# ---------------------------------------------------------------------------
# Idempotence — finalising twice from a known starting point
# ---------------------------------------------------------------------------


class TestIdempotence:
    """Releasing v1 then v2 leaves both sections in the right order."""

    def test_sequential_releases_stack_correctly(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        v1 = cl.finalize(text, version="1.0.0", date="2026-05-21")
        # Simulate the next dev cycle: a PR adds an Unreleased entry.
        v1_with_unreleased_entries = v1.replace(
            "## [Unreleased]\n\n## [1.0.0]",
            "## [Unreleased]\n\n### Added\n\n- post-1.0 feature.\n\n## [1.0.0]",
        )
        v2 = cl.finalize(v1_with_unreleased_entries, version="1.1.0", date="2026-06-01")
        # Both versioned sections exist in the expected order.
        v11_idx = v2.index("## [1.1.0]")
        v10_idx = v2.index("## [1.0.0]")
        assert v11_idx < v10_idx, "1.1.0 must sit above 1.0.0"
        # And the original 1.0.0 entries survive.
        assert "- new feature A." in v2
        assert "- post-1.0 feature." in v2
