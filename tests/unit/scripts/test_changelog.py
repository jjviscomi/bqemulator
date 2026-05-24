"""Unit tests for the CHANGELOG stamp tool.

Pins the contracts the release orchestrator relies on:

1. **Date-stamp** — a section authored as ``## [X.Y.Z]`` (no date)
   is rewritten to ``## [X.Y.Z] - YYYY-MM-DD`` in place; an
   existing date is overwritten so retries converge.
2. **Body preservation** — every byte outside the rewritten header
   line round-trips identically (preamble, bullets, prior versioned
   sections).
3. **Empty-section refusal** — a section with no bullet entries
   raises :class:`EmptySectionError`.
4. **No-section refusal** — a changelog with no versioned section
   raises :class:`NoSectionError`.
5. **Version mismatch refusal** — releasing X.Y.Z when the topmost
   section is some other version raises
   :class:`VersionMismatchError`.
6. **Strict version + date formats** — only canonical ``X.Y.Z`` and
   ``YYYY-MM-DD`` pass validation.
7. **CLI exit-code contract** — each error class maps to a stable,
   distinct exit code.

The script under test lives at
[`scripts/changelog.py`](../../../scripts/changelog.py).
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from scripts import changelog as cl

pytestmark = pytest.mark.unit


_PREAMBLE = """\
# Changelog

All notable changes to this project are documented in this file.

"""

_BODY = """\
### Added

- Expose the foo API.

### Fixed

- Fix the bar bug.
"""

_PRIOR = """\
## [0.1.0] - 2026-01-01

### Added

- Initial release.
"""


def _seed_changelog(
    tmp_path: Path,
    *,
    section_header: str = "## [1.0.0]",
    body: str = _BODY,
    has_prior: bool = True,
) -> Path:
    """Compose a minimal CHANGELOG.md with the supplied section and body."""
    sections = [_PREAMBLE]
    if section_header:
        sections.append(f"{section_header}\n\n")
        if body:
            sections.append(body)
            if not body.endswith("\n"):
                sections.append("\n")
            sections.append("\n")
    if has_prior:
        sections.append(_PRIOR)
    path = tmp_path / "CHANGELOG.md"
    path.write_text("".join(sections), encoding="utf-8")
    return path


class TestStampHappyPath:
    """The undated section gets stamped; bodies survive."""

    def test_stamps_date_into_undated_header(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert "## [1.0.0] - 2026-05-21" in updated
        assert "## [1.0.0]\n" not in updated  # no longer undated

    def test_overwrites_existing_date(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="## [1.0.0] - 2026-04-01")
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert "## [1.0.0] - 2026-05-21" in updated
        assert "2026-04-01" not in updated

    def test_body_round_trips(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert "- Expose the foo API." in updated
        assert "- Fix the bar bug." in updated

    def test_preamble_round_trips_byte_for_byte(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert updated.startswith(_PREAMBLE + "## [1.0.0] - 2026-05-21")

    def test_prior_versioned_section_survives(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert "## [0.1.0] - 2026-01-01" in updated
        assert "- Initial release." in updated

    def test_default_date_is_today_utc(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0")
        assert re.search(r"## \[1\.0\.0\] - \d{4}-\d{2}-\d{2}", updated)

    def test_idempotent_when_date_already_matches(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="## [1.0.0] - 2026-05-21")
        text = cl_path.read_text(encoding="utf-8")
        updated = cl.stamp(text, version="1.0.0", date="2026-05-21")
        assert text == updated


class TestStampErrorPaths:
    """``stamp`` rejects malformed or unsafe states cleanly."""

    def test_missing_section_raises(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="", has_prior=False)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(cl.NoSectionError):
            cl.stamp(text, version="1.0.0", date="2026-05-21")

    def test_empty_section_raises(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, body="")
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(cl.EmptySectionError):
            cl.stamp(text, version="1.0.0", date="2026-05-21")

    def test_section_with_subheadings_but_no_bullets_raises(self, tmp_path: Path) -> None:
        empty_subheadings = "### Added\n\n### Fixed\n"
        cl_path = _seed_changelog(tmp_path, body=empty_subheadings)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(cl.EmptySectionError):
            cl.stamp(text, version="1.0.0", date="2026-05-21")

    def test_version_mismatch_raises(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="## [1.0.0]")
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(
            cl.VersionMismatchError,
            match=r"\[1\.0\.0\].*but the release target is.*1\.1\.0",
        ):
            cl.stamp(text, version="1.1.0", date="2026-05-21")

    @pytest.mark.parametrize(
        "bad_version",
        ["1.0", "1.0.0.0", "1.0.0-rc1", "v1.0.0", "abc"],
    )
    def test_malformed_version_raises_value_error(self, tmp_path: Path, bad_version: str) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match=r"canonical X\.Y\.Z"):
            cl.stamp(text, version=bad_version, date="2026-05-21")

    @pytest.mark.parametrize(
        "bad_date",
        ["2026/05/21", "2026-5-21", "21-05-2026", "today"],
    )
    def test_malformed_date_raises_value_error(self, tmp_path: Path, bad_date: str) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="ISO-8601"):
            cl.stamp(text, version="1.0.0", date=bad_date)


class TestCli:
    """``main`` returns the documented exit codes."""

    def test_check_passes_and_does_not_write(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        before = cl_path.read_text(encoding="utf-8")
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--check", "--file", str(cl_path)])
        assert rc == cl.EXIT_OK
        assert cl_path.read_text(encoding="utf-8") == before

    def test_apply_writes_stamped_changelog(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_OK
        updated = cl_path.read_text(encoding="utf-8")
        assert "## [1.0.0] - 2026-05-21" in updated
        assert "- Expose the foo API." in updated

    def test_missing_section_returns_exit_3(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="", has_prior=False)
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_NO_SECTION

    def test_empty_section_returns_exit_4(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, body="")
        rc = cl.main(["1.0.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_EMPTY_SECTION

    def test_version_mismatch_returns_exit_5(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path, section_header="## [1.0.0]")
        rc = cl.main(["1.1.0", "--date", "2026-05-21", "--file", str(cl_path)])
        assert rc == cl.EXIT_VERSION_MISMATCH

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


class TestSequentialReleases:
    """Stamping a v1 release then a fresh v2 section preserves order."""

    def test_v2_section_above_v1(self, tmp_path: Path) -> None:
        cl_path = _seed_changelog(tmp_path)
        text = cl_path.read_text(encoding="utf-8")
        v1 = cl.stamp(text, version="1.0.0", date="2026-05-21")
        # Simulate the next release: the operator prepends a fresh
        # ``## [1.1.0]`` section above ``## [1.0.0]``.
        v1_with_new_section = v1.replace(
            "## [1.0.0] - 2026-05-21",
            "## [1.1.0]\n\n### Added\n\n- Post-1.0 feature.\n\n## [1.0.0] - 2026-05-21",
        )
        v2 = cl.stamp(v1_with_new_section, version="1.1.0", date="2026-06-01")
        v11_idx = v2.index("## [1.1.0] - 2026-06-01")
        v10_idx = v2.index("## [1.0.0] - 2026-05-21")
        assert v11_idx < v10_idx
        assert "- Post-1.0 feature." in v2
        assert "- Expose the foo API." in v2
