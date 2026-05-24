"""Unit tests for the release orchestrator (P4.c).

Pins the contracts the v1.0.0 release relies on:

1. **Hard preconditions** — the orchestrator refuses to run outside a
   git repo, on a dirty working tree, or when ``make verify`` fails.
2. **Plan composition** — ``compose_plan`` produces a deterministic
   ``ReleasePlan`` (commit message + tag name + dates) from a
   ``ReleaseOptions`` triple.
3. **Dry-run safety** — ``--dry-run`` mode never mutates files or
   git state; the existing ``__init__.py`` and ``CHANGELOG.md`` are
   byte-for-byte unchanged after the orchestrator returns.
4. **Apply mutation** — ``--apply`` mode writes the version bump,
   stamps the changelog, creates the release commit, and tags it.
5. **Exit-code contract** — each abort path maps to a distinct exit
   code so ``release.yml``-driven debugging can pin the failure point.
6. **Tool-not-found UX** — missing ``git`` / ``make`` raises a clean
   :class:`ToolMissingError` rather than a confusing
   ``FileNotFoundError`` from ``subprocess``.

Every test that needs a git repo creates a throw-away one under
``tmp_path`` via :func:`_init_repo`; tests never touch the user's
real working tree.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
from unittest import mock

import pytest
from scripts import bump_version as bump
from scripts import release as rel

pytestmark = pytest.mark.unit

# Resolved git binary used by the test helpers — keeps subprocess calls
# clear of S607 (partial executable path) without scattering ``noqa``
# directives across every helper.
_GIT = shutil.which("git") or "git"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_INIT_TEMPLATE = '''"""bqemulator package."""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
'''

_CHANGELOG_TEMPLATE = """\
# Changelog

## [0.2.0]

### Added

- Shiny new thing.

## [0.1.0] - 2026-01-01

### Added

- Initial release.
"""

_README_TEMPLATE = """# bqemulator

[![PyPI](https://img.shields.io/pypi/v/bqemulator.svg?cacheSeconds=120&v=0.1.0)](https://pypi.org/project/bqemulator/)
[![Python](https://img.shields.io/pypi/pyversions/bqemulator.svg?cacheSeconds=120&v=0.1.0)](https://pypi.org/project/bqemulator/)
"""


def _seed_repo_files(repo: Path, *, version: str = "0.1.0", with_readme: bool = False) -> None:
    """Drop a minimal source + changelog (+ optionally README) into ``repo``."""
    (repo / "src" / "bqemulator").mkdir(parents=True, exist_ok=True)
    init = repo / "src" / "bqemulator" / "__init__.py"
    init.write_text(_INIT_TEMPLATE.replace("0.1.0", version), encoding="utf-8")
    changelog = repo / "CHANGELOG.md"
    changelog.write_text(_CHANGELOG_TEMPLATE, encoding="utf-8")
    if with_readme:
        (repo / "README.md").write_text(
            _README_TEMPLATE.replace("0.1.0", version),
            encoding="utf-8",
        )


def _init_repo(tmp_path: Path, *, version: str = "0.1.0", with_readme: bool = False) -> Path:
    """Create a git repo at ``tmp_path/repo`` with a clean initial commit.

    Returns the repo root. Uses ``-c`` config flags rather than mutating
    global git config so the test never leaks state into the operator's
    ``~/.gitconfig``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo_files(repo, version=version, with_readme=with_readme)
    env_cfg = [
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "init.defaultBranch=main",
    ]
    subprocess.run([_GIT, "init", "-q", "-b", "main"], cwd=repo, check=True)  # noqa: S603
    subprocess.run([_GIT, *env_cfg, "add", "-A"], cwd=repo, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [_GIT, *env_cfg, "commit", "-q", "-m", "initial"],
        cwd=repo,
        check=True,
    )
    return repo


def _opts(
    repo: Path,
    *,
    dry_run: bool = True,
    explicit_version: str | None = None,
    bump_kind: str | None = "minor",
    skip_verify: bool = True,
    release_date: str | None = "2026-05-21",
) -> rel.ReleaseOptions:
    """Build :class:`ReleaseOptions` with sensible test defaults."""
    return rel.ReleaseOptions(
        repo_root=repo,
        dry_run=dry_run,
        skip_verify=skip_verify,
        explicit_version=explicit_version,
        bump_kind=bump_kind,
        release_date=release_date,
    )


# A tiny git-config helper so apply-mode tests can land commits without
# leaking into the operator's global gitconfig. Tests that need to make
# git commits via the orchestrator wrap the call in a context that sets
# GIT_AUTHOR_*/GIT_COMMITTER_*/commit.gpgsign=false in os.environ.
def _git_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch git environment so commits work in CI without a global config."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


# ---------------------------------------------------------------------------
# Plan composition
# ---------------------------------------------------------------------------


class TestComposePlan:
    """``compose_plan`` builds the deterministic ReleasePlan."""

    def test_bump_kind_minor_increments_minor(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, version="0.1.0")
        plan = rel.compose_plan(_opts(repo, bump_kind="minor"))
        assert str(plan.current) == "0.1.0"
        assert str(plan.target) == "0.2.0"
        assert plan.commit_message == "release: bump to v0.2.0"
        assert plan.tag_name == "v0.2.0"
        assert plan.release_date == "2026-05-21"

    def test_explicit_version_wins(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        plan = rel.compose_plan(_opts(repo, explicit_version="1.0.0", bump_kind=None))
        assert plan.tag_name == "v1.0.0"

    def test_release_date_defaults_to_today(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        plan = rel.compose_plan(_opts(repo, release_date=None))
        # Today (UTC) — we assert format only.
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", plan.release_date)

    def test_backward_version_raises(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, version="0.5.0")
        with pytest.raises(bump.VersionNotGreaterError):
            rel.compose_plan(_opts(repo, explicit_version="0.1.0", bump_kind=None))


# ---------------------------------------------------------------------------
# Hard preconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    """orchestrate refuses to run on an unsafe baseline."""

    def test_no_git_repo_returns_exit_10(self, tmp_path: Path) -> None:
        # tmp_path has no .git
        _seed_repo_files(tmp_path)
        rc = rel.orchestrate(_opts(tmp_path))
        assert rc == rel.EXIT_NOT_A_REPO

    def test_dirty_tree_returns_exit_11(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        # Make the tree dirty.
        (repo / "src" / "bqemulator" / "__init__.py").write_text(
            '__version__ = "0.1.0"\n# dirty\n', encoding="utf-8"
        )
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_DIRTY_TREE

    def test_verify_failure_returns_exit_12(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)

        with mock.patch.object(rel, "run_make_verify", return_value=2):
            rc = rel.orchestrate(_opts(repo, skip_verify=False))
        assert rc == rel.EXIT_VERIFY_FAILED

    def test_verify_succeeds_when_make_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)

        with mock.patch.object(rel, "run_make_verify", return_value=0):
            rc = rel.orchestrate(_opts(repo, skip_verify=False, dry_run=True))
        assert rc == rel.EXIT_OK


# ---------------------------------------------------------------------------
# Dry-run safety
# ---------------------------------------------------------------------------


class TestDryRun:
    """``--dry-run`` mode never mutates files or git state."""

    def test_dry_run_does_not_touch_init_py(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        init = repo / "src" / "bqemulator" / "__init__.py"
        before = init.read_text(encoding="utf-8")
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        assert init.read_text(encoding="utf-8") == before

    def test_dry_run_does_not_touch_changelog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        changelog = repo / "CHANGELOG.md"
        before = changelog.read_text(encoding="utf-8")
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        assert changelog.read_text(encoding="utf-8") == before

    def test_dry_run_does_not_create_tag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        tags = subprocess.run(  # noqa: S603
            [_GIT, "tag"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert tags.stdout.strip() == ""

    def test_dry_run_emits_proposed_commit_and_tag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rel.orchestrate(_opts(repo))
        captured = capsys.readouterr()
        assert "would run: git add -A" in captured.out
        assert "release: bump to v0.2.0" in captured.out
        assert "would run: git tag" in captured.out
        assert "v0.2.0" in captured.out

    def test_dry_run_returns_changelog_failed_on_value_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A malformed date surfaces from cl.stamp as ValueError; the dry-run
        preview path must catch it symmetrically with the apply path and exit
        EXIT_CHANGELOG_FAILED rather than crashing with an unhandled exception.
        """
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo, release_date="not-a-date"))
        assert rc == rel.EXIT_CHANGELOG_FAILED


# ---------------------------------------------------------------------------
# Apply mutation + commit + tag
# ---------------------------------------------------------------------------


class TestApply:
    """``--apply`` mode writes files, commits, and tags."""

    def test_apply_writes_new_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        init = repo / "src" / "bqemulator" / "__init__.py"
        assert '"0.2.0"' in init.read_text(encoding="utf-8")

    def test_apply_stamps_changelog(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "## [0.2.0] - 2026-05-21" in changelog
        assert "- Shiny new thing." in changelog

    def test_apply_creates_commit_and_tag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        tags = subprocess.run(  # noqa: S603
            [_GIT, "tag"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "v0.2.0" in tags.stdout
        log = subprocess.run(  # noqa: S603
            [_GIT, "log", "-1", "--format=%s"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert log.stdout.strip() == "release: bump to v0.2.0"

    def test_apply_leaves_tree_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        status = subprocess.run(  # noqa: S603
            [_GIT, "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert status.stdout == ""

    def test_apply_refuses_empty_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.2.0]\n\n", encoding="utf-8")
        subprocess.run([_GIT, "add", "-A"], cwd=repo, check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [_GIT, "commit", "-q", "-m", "empty section"],
            cwd=repo,
            check=True,
        )
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_CHANGELOG_FAILED

    def test_apply_refuses_missing_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n", encoding="utf-8")
        subprocess.run([_GIT, "add", "-A"], cwd=repo, check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [_GIT, "commit", "-q", "-m", "no section"],
            cwd=repo,
            check=True,
        )
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_CHANGELOG_FAILED

    def test_apply_refuses_version_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        # Author a section for the wrong version (1.0.0 instead of 0.2.0).
        (repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.0.0]\n\n### Added\n\n- Wrong version.\n",
            encoding="utf-8",
        )
        subprocess.run([_GIT, "add", "-A"], cwd=repo, check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [_GIT, "commit", "-q", "-m", "wrong section version"],
            cwd=repo,
            check=True,
        )
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_CHANGELOG_FAILED


# ---------------------------------------------------------------------------
# README badge cache-bust integration
# ---------------------------------------------------------------------------


class TestReadmeBadgeBump:
    """The orchestrator bumps README cache-bust badges in lockstep with the
    version. Both dry-run preview and apply mutation are exercised, plus the
    no-README path that the existing tests above (with_readme=False) silently
    cover.
    """

    def test_dry_run_previews_badge_bump(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path, with_readme=True)
        readme = repo / "README.md"
        before = readme.read_text(encoding="utf-8")
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        captured = capsys.readouterr()
        assert "would bump 2 README badge(s)" in captured.out
        # Dry-run is read-only — README must be unchanged.
        assert readme.read_text(encoding="utf-8") == before

    def test_dry_run_no_readme_emits_no_badge_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path, with_readme=False)
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        captured = capsys.readouterr()
        # The orchestrator skips silently when README is absent — no
        # "would bump N README" or "no cache-bust badges" message at all.
        assert "README" not in captured.out

    def test_dry_run_readme_without_badges_emits_no_badge_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path, with_readme=False)
        (repo / "README.md").write_text("# Plain README\n", encoding="utf-8")
        subprocess.run([_GIT, "add", "-A"], cwd=repo, check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [_GIT, "commit", "-q", "-m", "add bare README"],
            cwd=repo,
            check=True,
        )
        rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_OK
        captured = capsys.readouterr()
        assert "no cache-bust badges to bump" in captured.out

    def test_apply_mutates_readme_badge(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path, with_readme=True)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        body = (repo / "README.md").read_text(encoding="utf-8")
        assert "v=0.1.0" not in body
        assert body.count("v=0.2.0") == 2

    def test_apply_readme_change_is_in_release_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path, with_readme=True)
        rc = rel.orchestrate(_opts(repo, dry_run=False))
        assert rc == rel.EXIT_OK
        # ``git show --stat HEAD`` must list README.md alongside __init__.py
        # — proves the release commit captures both bumps atomically.
        files_in_commit = subprocess.run(  # noqa: S603
            [_GIT, "show", "--name-only", "--format=", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        names = set(files_in_commit.stdout.split())
        assert "README.md" in names
        assert "src/bqemulator/__init__.py" in names
        assert "CHANGELOG.md" in names


# ---------------------------------------------------------------------------
# Tool-resolution UX
# ---------------------------------------------------------------------------


class TestToolResolution:
    """Missing git / make raises :class:`ToolMissingError`."""

    def test_resolve_tool_returns_absolute_path(self) -> None:
        path = rel._resolve_tool("git")
        assert Path(path).is_absolute()

    def test_resolve_tool_raises_on_missing(self) -> None:
        with pytest.raises(rel.ToolMissingError, match="not found on PATH"):
            rel._resolve_tool("this-binary-cannot-possibly-exist-12345")

    def test_missing_git_returns_not_a_repo_exit_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)

        with mock.patch.object(
            rel, "_resolve_tool", side_effect=rel.ToolMissingError("git missing")
        ):
            rc = rel.orchestrate(_opts(repo))
        assert rc == rel.EXIT_NOT_A_REPO


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCli:
    """``main`` enforces argument shape."""

    def test_missing_version_or_next_raises_systemexit(self, tmp_path: Path) -> None:
        # Without --version or --next, argparse exits with code 2.
        repo = _init_repo(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            rel.main(["--repo-root", str(repo)])
        assert excinfo.value.code == 2

    def test_mutually_exclusive_version_and_next(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(SystemExit):
            rel.main(
                [
                    "--repo-root",
                    str(repo),
                    "--version",
                    "1.0.0",
                    "--next",
                    "minor",
                ]
            )

    def test_default_mode_is_dry_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        init = repo / "src" / "bqemulator" / "__init__.py"
        before = init.read_text(encoding="utf-8")
        rc = rel.main(
            [
                "--repo-root",
                str(repo),
                "--next",
                "minor",
                "--skip-verify",
                "--date",
                "2026-05-21",
            ]
        )
        assert rc == rel.EXIT_OK
        # Default is dry-run → file unchanged.
        assert init.read_text(encoding="utf-8") == before

    def test_apply_flag_mutates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _git_test_env(monkeypatch)
        repo = _init_repo(tmp_path)
        rc = rel.main(
            [
                "--repo-root",
                str(repo),
                "--apply",
                "--next",
                "minor",
                "--skip-verify",
                "--date",
                "2026-05-21",
            ]
        )
        assert rc == rel.EXIT_OK
        init = (repo / "src" / "bqemulator" / "__init__.py").read_text(encoding="utf-8")
        assert '"0.2.0"' in init
