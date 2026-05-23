#!/usr/bin/env python3
"""Orchestrate a bqemulator release: verify, bump, finalize changelog, commit, tag.

P4.c (2026-05-21) — third leg of the release-tooling triad
(`bump_version.py` + `changelog.py` + this script). Wraps the three
pieces of an end-to-end release in a single guarded entry point.

Usage::

    python scripts/release.py --dry-run --next minor
    python scripts/release.py --dry-run --version 1.0.0
    python scripts/release.py --apply --next patch
    python scripts/release.py --apply --version 1.0.0 --skip-verify

The default mode is ``--dry-run`` — it runs every read-only check
(working-tree clean, ``make verify``) and renders a preview of the
proposed ``__init__.py`` + ``CHANGELOG.md`` mutations, the commit
message, and the tag name, without writing any file or touching git.
``--apply`` runs the full pipeline including the commit and tag.

Steps (in ``--apply`` mode):

1. Verify the working tree is clean (``git status --porcelain``).
2. Compute the target version from ``--version`` or ``--next``.
3. Run ``make verify`` (full release gate chain), unless
   ``--skip-verify`` is passed.
4. Bump ``__version__`` via :mod:`scripts.bump_version`.
5. Finalise ``CHANGELOG.md`` via :mod:`scripts.changelog`.
6. ``git add -A && git commit -m "release: bump to vX.Y.Z"``.
7. ``git tag vX.Y.Z`` (signed if ``git config commit.gpgsign true``).
8. Print push instructions — the operator is responsible for the
   actual ``git push origin vX.Y.Z`` step. The release workflow
   (``.github/workflows/release.yml``) takes over once the tag lands
   on the remote.

In ``--dry-run`` mode, steps 4-7 are simulated: the script computes
what each step would do and prints a diff-style preview to stdout, but
no files are modified and no git commands run beyond the read-only
``git status``.

Hard preconditions (the script aborts on any of these):

- Not inside a git repository (``.git`` directory absent).
- Working tree has uncommitted changes.
- ``make verify`` exits non-zero.
- The computed version is not strictly greater than the current.
- ``CHANGELOG.md``'s ``Unreleased`` section is empty (use
  ``--allow-empty-changelog`` only for true zero-impact patches).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import bump_version as bump  # noqa: E402
from scripts import changelog as cl  # noqa: E402

#: Pinned commit subject — kept as a module-level constant so the unit
#: tests can assert the exact rendering without re-encoding the spec.
COMMIT_MESSAGE_TEMPLATE = "release: bump to v{version}"

#: Pinned tag template (``v`` prefix per [semver convention](https://semver.org/)).
TAG_TEMPLATE = "v{version}"

#: CLI exit codes (callable: each maps to a distinct failure mode so
#: ``release.yml``-driven debugging can pin the abort point).
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_A_REPO = 10
EXIT_DIRTY_TREE = 11
EXIT_VERIFY_FAILED = 12
EXIT_BUMP_FAILED = 13
EXIT_CHANGELOG_FAILED = 14
EXIT_COMMIT_FAILED = 15
EXIT_TAG_FAILED = 16


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ReleasePlan:
    """All decisions made up-front so dry-run and apply walk the same path."""

    current: bump.Version
    target: bump.Version
    commit_message: str
    tag_name: str
    release_date: str

    @property
    def is_dry_run(self) -> bool:
        """Always False on the orchestrator data — the flag lives on argparse."""
        return False  # placeholder for symmetric callers


@dataclass(slots=True, frozen=True)
class ReleaseOptions:
    """Argparse-derived options passed through the orchestrator."""

    repo_root: Path
    dry_run: bool
    skip_verify: bool
    allow_empty_changelog: bool
    explicit_version: str | None
    bump_kind: str | None
    release_date: str | None


# ---------------------------------------------------------------------------
# Subprocess helpers (kept thin for ease of mocking)
# ---------------------------------------------------------------------------


class ToolMissingError(RuntimeError):
    """Raised when a required external tool (git / make) is not on PATH."""


def _resolve_tool(name: str) -> str:
    """Resolve ``name`` to an absolute path on PATH or raise :class:`ToolMissingError`.

    Resolution via :func:`shutil.which` lets the orchestrator give a
    clean error message (rather than a confusing ``FileNotFoundError``
    from inside ``subprocess.run``) when the dev environment is
    missing git / make.
    """
    resolved = shutil.which(name)
    if resolved is None:
        msg = f"required tool not found on PATH: {name}"
        raise ToolMissingError(msg)
    return resolved


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in ``cwd``, capturing stdout + stderr.

    The first element must be an absolute path (resolved via
    :func:`_resolve_tool`). Returns the ``CompletedProcess``; callers
    inspect ``returncode``. No automatic ``check=True`` so the
    orchestrator can map non-zero exits to dedicated exit codes.
    """
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(repo_root: Path) -> bool:
    """Return True when ``repo_root`` contains a ``.git`` directory or file.

    ``.git`` is a directory for normal repos and a file for git-worktree
    submodules — both shapes count as "this is a git checkout."
    """
    git_path = repo_root / ".git"
    return git_path.exists()


def working_tree_status(repo_root: Path) -> str:
    """Return ``git status --porcelain`` output (empty string = clean).

    Raises :class:`RuntimeError` if git itself fails — distinct from a
    dirty tree, which manifests as non-empty stdout with ``returncode=0``.
    """
    git = _resolve_tool("git")
    result = _run([git, "status", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        msg = f"git status failed: {result.stderr.strip()}"
        raise RuntimeError(msg)
    return result.stdout


def run_make_verify(repo_root: Path) -> int:
    """Invoke ``make verify`` in ``repo_root``. Returns the exit code."""
    make = _resolve_tool("make")
    # ``make verify`` streams to the terminal directly — the orchestrator
    # exits early on a non-zero return without holding the entire log in
    # memory. ``capture_output`` would buffer the multi-minute output.
    result = subprocess.run(  # noqa: S603
        [make, "verify"],
        cwd=repo_root,
        check=False,
    )
    return result.returncode


def git_commit(repo_root: Path, *, message: str) -> int:
    """Stage all changes and create a release commit. Returns git's exit code."""
    git = _resolve_tool("git")
    add = _run([git, "add", "-A"], cwd=repo_root)
    if add.returncode != 0:
        return add.returncode
    commit = _run([git, "commit", "-m", message], cwd=repo_root)
    return commit.returncode


def git_tag(repo_root: Path, *, name: str) -> int:
    """Create an annotated tag ``name``. Returns git's exit code.

    The tag is annotated (``git tag -a``) so it carries the operator's
    identity + the message body. The body is the same as the commit
    subject — the GitHub release UI auto-derives release notes from
    ``release.yml`` so the tag annotation is intentionally short.

    When ``git config commit.gpgsign true`` is set globally, the
    annotated tag is signed automatically. The orchestrator does not
    force the ``-s`` flag — that is the operator's choice in
    ``gitconfig``.
    """
    git = _resolve_tool("git")
    result = _run(
        [git, "tag", "-a", name, "-m", name],
        cwd=repo_root,
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Plan composition
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    """Return today's UTC date in ``YYYY-MM-DD``."""
    return cl._today_iso()  # noqa: SLF001 — reuse the validated formatter


def compose_plan(opts: ReleaseOptions) -> ReleasePlan:
    """Read the current version and compute the release plan.

    Raises :class:`bump.VersionFormatError` / :class:`ValueError` /
    :class:`bump.VersionNotGreaterError` on malformed input. The caller
    is responsible for mapping these to CLI exit codes.
    """
    version_file = opts.repo_root / "src" / "bqemulator" / "__init__.py"
    current = bump.read_current(version_file)
    target = bump.resolve_target(
        current,
        explicit=opts.explicit_version,
        bump_kind=opts.bump_kind,
    )
    release_date = opts.release_date if opts.release_date is not None else _today_iso()
    return ReleasePlan(
        current=current,
        target=target,
        commit_message=COMMIT_MESSAGE_TEMPLATE.format(version=target),
        tag_name=TAG_TEMPLATE.format(version=target),
        release_date=release_date,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _emit(line: str, *, prefix: str | None = None) -> None:
    """Print a line, optionally prefixed (``[dry-run]`` for the dry-run trail)."""
    if prefix is None:
        print(line)
    else:
        print(f"{prefix} {line}")


def _preview_changelog(plan: ReleasePlan, opts: ReleaseOptions) -> None:
    """Render a one-line summary of the proposed CHANGELOG mutation."""
    changelog_path = opts.repo_root / "CHANGELOG.md"
    text = changelog_path.read_text(encoding="utf-8")
    try:
        cl.finalize(
            text,
            version=str(plan.target),
            date=plan.release_date,
            allow_empty=opts.allow_empty_changelog,
        )
    except cl.ChangelogError as exc:
        msg = f"changelog preview failed: {exc}"
        raise RuntimeError(msg) from exc
    _emit(
        f"would finalise CHANGELOG.md: [Unreleased] -> [{plan.target}] — {plan.release_date}",
        prefix="[dry-run]",
    )


def _preview_bump(plan: ReleasePlan, opts: ReleaseOptions) -> None:
    """Render the proposed version bump and README badge cache-bust diff."""
    _emit(
        f"would bump __version__: {plan.current} -> {plan.target}",
        prefix="[dry-run]",
    )
    readme_path = opts.repo_root / "README.md"
    if readme_path.exists():
        text = readme_path.read_text(encoding="utf-8")
        _, count = bump.update_readme_text(text, plan.target)
        if count:
            _emit(
                f"would bump {count} README badge(s) in {readme_path.name}: "
                f"?v={plan.current} -> ?v={plan.target}",
                prefix="[dry-run]",
            )
        else:
            _emit(
                f"README has no cache-bust badges to bump in {readme_path.name}",
                prefix="[dry-run]",
            )


def _preview_commit_and_tag(plan: ReleasePlan) -> None:
    """Render the commit + tag operations the apply mode would run."""
    _emit(f'would run: git add -A && git commit -m "{plan.commit_message}"', prefix="[dry-run]")
    _emit(f"would run: git tag -a {plan.tag_name} -m {plan.tag_name}", prefix="[dry-run]")


def _apply_bump(plan: ReleasePlan, opts: ReleaseOptions) -> int:
    """Write the new version to ``__init__.py`` + bump README badge cache-bust."""
    version_file = opts.repo_root / "src" / "bqemulator" / "__init__.py"
    try:
        bump.write_new(plan.target, version_file)
    except bump.VersionFormatError as exc:
        print(f"error: bump failed: {exc}", file=sys.stderr)
        return EXIT_BUMP_FAILED
    _emit(f"bumped __version__: {plan.current} -> {plan.target}")
    readme_path = opts.repo_root / "README.md"
    readme_count = bump.write_readme_badges(plan.target, readme_path)
    if readme_count:
        _emit(f"bumped {readme_count} README badge(s) in {readme_path.name}")
    return EXIT_OK


def _apply_changelog(plan: ReleasePlan, opts: ReleaseOptions) -> int:
    """Rewrite ``CHANGELOG.md`` to promote Unreleased into a versioned section."""
    changelog_path = opts.repo_root / "CHANGELOG.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
        updated = cl.finalize(
            text,
            version=str(plan.target),
            date=plan.release_date,
            allow_empty=opts.allow_empty_changelog,
        )
        changelog_path.write_text(updated, encoding="utf-8")
    except (cl.ChangelogError, ValueError) as exc:
        print(f"error: changelog finalisation failed: {exc}", file=sys.stderr)
        return EXIT_CHANGELOG_FAILED
    _emit(f"finalised CHANGELOG: [Unreleased] -> [{plan.target}] — {plan.release_date}")
    return EXIT_OK


def _apply_commit_and_tag(plan: ReleasePlan, opts: ReleaseOptions) -> int:
    """Stage the bump + changelog changes and create the release commit + tag."""
    try:
        rc = git_commit(opts.repo_root, message=plan.commit_message)
    except ToolMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_COMMIT_FAILED
    if rc != 0:
        print(f"error: git commit failed (rc={rc})", file=sys.stderr)
        return EXIT_COMMIT_FAILED
    _emit(f'committed: "{plan.commit_message}"')

    try:
        rc = git_tag(opts.repo_root, name=plan.tag_name)
    except ToolMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_TAG_FAILED
    if rc != 0:
        print(f"error: git tag failed (rc={rc})", file=sys.stderr)
        return EXIT_TAG_FAILED
    _emit(f"tagged: {plan.tag_name}")
    return EXIT_OK


def _print_next_steps(plan: ReleasePlan, *, dry_run: bool) -> None:
    """Operator-facing instructions for the manual ``git push`` step."""
    if dry_run:
        _emit("")
        _emit("Dry-run complete. No files modified, no git state changed.")
        _emit(f"Re-run with --apply to commit and tag {plan.tag_name}.")
        return
    _emit("")
    _emit("Release commit + tag prepared locally. Next steps:")
    _emit(f"  1. Inspect: git show {plan.tag_name}")
    _emit(f"  2. Push:    git push origin main {plan.tag_name}")
    _emit(
        "  3. .github/workflows/release.yml takes over for PyPI + GHCR + GitHub release.",
    )
    _emit("")
    _emit("Tags are immutable on GitHub. If you need to abandon this release:")
    _emit(f"  git tag -d {plan.tag_name}")
    _emit("  git reset --hard HEAD~1   # discard the release commit")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def orchestrate(opts: ReleaseOptions) -> int:
    """Run the release pipeline. Returns the appropriate exit code."""
    # Step 1: detect git repo.
    if not is_git_repo(opts.repo_root):
        print(
            f"error: {opts.repo_root} is not a git repository (.git directory absent). "
            "The release flow requires git for the commit + tag steps.",
            file=sys.stderr,
        )
        return EXIT_NOT_A_REPO

    # Step 2: clean working tree.
    try:
        status = working_tree_status(opts.repo_root)
    except ToolMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_A_REPO
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_A_REPO
    if status.strip():
        print(
            "error: working tree is not clean. Commit or stash changes before release:\n" + status,
            file=sys.stderr,
        )
        return EXIT_DIRTY_TREE

    # Step 3: compose plan.
    try:
        plan = compose_plan(opts)
    except (bump.VersionFormatError, bump.VersionNotGreaterError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    _emit(f"current version: {plan.current}")
    _emit(f"target version:  {plan.target}")
    _emit(f"release date:    {plan.release_date}")
    _emit(f"commit message:  {plan.commit_message}")
    _emit(f"tag:             {plan.tag_name}")
    _emit("")

    # Step 4: run make verify (unless skipped). Both modes run this —
    # an unverified tree is never safe to release, even in dry-run we
    # want operator-visible failure.
    if opts.skip_verify:
        _emit("skip-verify=true; not running ``make verify`` (NOT RECOMMENDED)")
    else:
        _emit("running ``make verify`` (full gate chain)...")
        try:
            rc = run_make_verify(opts.repo_root)
        except ToolMissingError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_VERIFY_FAILED
        if rc != 0:
            print(
                f"error: make verify failed (rc={rc}). Refusing to release.",
                file=sys.stderr,
            )
            return EXIT_VERIFY_FAILED
        _emit("``make verify`` passed.")
    _emit("")

    # Step 5+: bump + changelog. Dry-run previews; apply mutates.
    if opts.dry_run:
        _preview_bump(plan, opts)
        try:
            _preview_changelog(plan, opts)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_CHANGELOG_FAILED
        _preview_commit_and_tag(plan)
        _print_next_steps(plan, dry_run=True)
        return EXIT_OK

    # --apply mode beyond this point.
    rc = _apply_bump(plan, opts)
    if rc != EXIT_OK:
        return rc

    rc = _apply_changelog(plan, opts)
    if rc != EXIT_OK:
        return rc

    rc = _apply_commit_and_tag(plan, opts)
    if rc != EXIT_OK:
        return rc

    _print_next_steps(plan, dry_run=False)
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Factored for test access."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "Orchestrate a release.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help=(
            "Preview the release without mutating files or git state "
            "(default). Runs ``make verify`` to validate the gate chain."
        ),
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Run the full release pipeline: verify, bump, changelog, commit, tag.",
    )
    version_group = parser.add_mutually_exclusive_group()
    version_group.add_argument(
        "--version",
        dest="explicit_version",
        default=None,
        help="Explicit canonical X.Y.Z to release. Mutually exclusive with --next.",
    )
    version_group.add_argument(
        "--next",
        choices=bump.BUMP_KINDS,
        dest="bump_kind",
        default=None,
        help="Bump kind: major / minor / patch. Mutually exclusive with --version.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Release date in ISO-8601 (YYYY-MM-DD). Defaults to today (UTC).",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip ``make verify``. NOT RECOMMENDED — only for debugging release tooling.",
    )
    parser.add_argument(
        "--allow-empty-changelog",
        action="store_true",
        help=(
            "Allow finalising the CHANGELOG when Unreleased is empty. Only "
            "appropriate for zero-impact patches (the new section will say "
            f"``{cl.PLACEHOLDER_BODY}``)."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root (default: %(default)s).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --apply takes precedence over the dry-run default.
    dry_run = not args.apply
    if args.explicit_version is None and args.bump_kind is None:
        parser.error(
            "must supply either --version X.Y.Z or --next {major,minor,patch}",
        )

    opts = ReleaseOptions(
        repo_root=args.repo_root,
        dry_run=dry_run,
        skip_verify=args.skip_verify,
        allow_empty_changelog=args.allow_empty_changelog,
        explicit_version=args.explicit_version,
        bump_kind=args.bump_kind,
        release_date=args.date,
    )
    return orchestrate(opts)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
