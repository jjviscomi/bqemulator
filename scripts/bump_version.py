#!/usr/bin/env python3
"""Bump bqemulator's ``__version__`` ahead of a release.

P4.c (2026-05-21) — first leg of the release-tooling triad (P5
prerequisite). The version lives in a single source of truth at
[`src/bqemulator/__init__.py`](../src/bqemulator/__init__.py) — hatchling
re-uses it through `[tool.hatch.version] path = ...` in
[`pyproject.toml`](../pyproject.toml), so the canonical bump touches one
file. A secondary in-place rewrite on
[`README.md`](../README.md) keeps the shields.io PyPI / Python-versions
badges' ``?cacheSeconds=120&v=X.Y.Z`` cache-bust query parameter in
sync — that suffix changes GitHub camo's cache key so README readers
see the new wheel without waiting ~24 h for the proxy to expire. The
README rewrite is idempotent (no-op when the badges are already at the
new version) and silent when the pattern is absent.

Usage::

    python scripts/bump_version.py 1.0.0            # explicit
    python scripts/bump_version.py --major          # 0.1.0 → 1.0.0
    python scripts/bump_version.py --minor          # 0.1.0 → 0.2.0
    python scripts/bump_version.py --patch          # 0.1.0 → 0.1.1
    python scripts/bump_version.py --print          # report current; no mutation
    python scripts/bump_version.py --next minor --check
                                                    # validate, don't write

The new version must be strictly greater than the current; semver
comparison is field-wise on ``(major, minor, patch)``. Pre-release
identifiers (e.g. ``1.0.0-rc1``) are intentionally rejected — the
release flow ships canonical ``MAJOR.MINOR.PATCH`` tags only (see
[release-process.md](../docs/architecture/contributing/release-process.md)).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Canonical version source — every other consumer (pyproject hatchling
#: backend, ``bqemulator.__version__``, mike-driven docs deploy) reads
#: this file. Hard-coding the path keeps the script self-contained.
VERSION_FILE = _REPO_ROOT / "src" / "bqemulator" / "__init__.py"

#: README that hosts the shields.io PyPI + Python-versions badges
#: bumped in lockstep with ``__version__``. The badge URL pattern
#: matched by :data:`_README_BADGE_RE` carries a
#: ``?cacheSeconds=120&v=X.Y.Z`` suffix; ``v=`` is a cache-bust query
#: parameter the shields.io endpoint ignores but GitHub camo (the
#: image proxy) keys its cache on, so changing it forces readers'
#: browsers to fetch the fresh PyPI version immediately on release.
README_FILE = _REPO_ROOT / "README.md"

#: Match ``__version__ = "X.Y.Z"`` exactly (single or double quotes).
#: The trailing group captures the canonical ``MAJOR.MINOR.PATCH`` form;
#: prerelease + build-metadata suffixes are intentionally rejected.
_VERSION_LINE_RE = re.compile(
    r'(?P<prefix>^__version__\s*=\s*["\'])(?P<version>\d+\.\d+\.\d+)(?P<suffix>["\'])',
    re.MULTILINE,
)

#: Strict ``X.Y.Z`` regex used by :func:`parse_version`. Rejects pre-
#: releases and build metadata so the tag namespace stays flat.
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

#: Match the cache-bust suffix on README badge URLs. Group 1 captures
#: the constant ``?cacheSeconds=<n>&v=`` prefix; the trailing triple is
#: the version we replace. ``\d+`` on cacheSeconds avoids hard-coding
#: the current ``120`` value — a future tuning of the camo TTL won't
#: silently de-match the regex.
_README_BADGE_RE = re.compile(r"(\?cacheSeconds=\d+&v=)\d+\.\d+\.\d+")

#: Bump kinds accepted on the CLI as ``--next <kind>``. Order matters
#: for argparse's ``choices`` display.
BumpKind = str  # Literal["major", "minor", "patch"] in spirit
BUMP_KINDS: tuple[str, ...] = ("major", "minor", "patch")

#: CLI exit codes.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_GREATER = 3


class VersionFormatError(ValueError):
    """Raised when a version string does not match strict ``X.Y.Z``."""


class VersionNotGreaterError(ValueError):
    """Raised when the proposed new version does not strictly exceed the current one."""


@dataclass(slots=True, frozen=True, order=True)
class Version:
    """Strict ``MAJOR.MINOR.PATCH`` triple with lexicographic ordering.

    ``order=True`` derives ``__lt__`` / ``__gt__`` from the field tuple,
    so ``Version(0, 1, 0) < Version(0, 2, 0)`` is field-wise correct.
    """

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        """Render canonical ``MAJOR.MINOR.PATCH``."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def bumped(self, kind: BumpKind) -> Version:
        """Return the next version under ``kind`` (``major`` / ``minor`` / ``patch``).

        Bumping ``major`` zeros minor + patch; bumping ``minor`` zeros
        patch — the standard semver-roll behaviour matching
        ``cargo`` / ``poetry`` / npm semver.
        """
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        msg = f"unknown bump kind: {kind!r} (expected one of {BUMP_KINDS})"
        raise ValueError(msg)


def parse_version(s: str) -> Version:
    """Parse ``"X.Y.Z"`` into a :class:`Version`. Raises on malformed input."""
    match = _VERSION_RE.match(s)
    if not match:
        msg = f"not a canonical X.Y.Z version: {s!r}"
        raise VersionFormatError(msg)
    return Version(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def read_current(file: Path = VERSION_FILE) -> Version:
    """Read ``__version__`` out of the canonical source file.

    Raises :class:`FileNotFoundError` when the file is missing and
    :class:`VersionFormatError` when the ``__version__ = "..."`` line
    is absent or malformed.
    """
    text = file.read_text(encoding="utf-8")
    match = _VERSION_LINE_RE.search(text)
    if not match:
        msg = (
            f'no canonical ``__version__ = "X.Y.Z"`` line found in {file}. '
            "The bump script requires the exact single-line form so the "
            "regex substitution is idempotent."
        )
        raise VersionFormatError(msg)
    return parse_version(match.group("version"))


def write_new(new: Version, file: Path = VERSION_FILE) -> Version:
    """Rewrite the ``__version__`` line to ``new``; return the old version.

    The substitution preserves every byte outside the captured version
    triple (quoting, surrounding whitespace, the rest of the module).
    """
    text = file.read_text(encoding="utf-8")
    match = _VERSION_LINE_RE.search(text)
    if not match:
        msg = f"no ``__version__ = ...`` line in {file}; cannot bump."
        raise VersionFormatError(msg)
    old = parse_version(match.group("version"))
    replacement = f"{match.group('prefix')}{new}{match.group('suffix')}"
    updated = text[: match.start()] + replacement + text[match.end() :]
    file.write_text(updated, encoding="utf-8")
    return old


def update_readme_text(text: str, new: Version) -> tuple[str, int]:
    """Rewrite every cache-bust badge URL in ``text`` to point at ``new``.

    Pure (no I/O): returns the rewritten body and the number of badges
    rewritten. ``count == 0`` means the README had no matching
    ``?cacheSeconds=...&v=X.Y.Z`` suffix; ``count == n`` for the same
    version is still idempotent (the substituted bytes equal the
    originals). Callers use the count purely for operator-facing
    reporting — a missing badge pattern is not an error.
    """
    return _README_BADGE_RE.subn(rf"\g<1>{new}", text)


def write_readme_badges(new: Version, file: Path = README_FILE) -> int:
    """Update the README cache-bust badges to ``new``. Return # **written**.

    The return value counts badges whose **bytes actually changed on disk**,
    not regex matches. That distinction matters because ``main()`` and
    the orchestrator emit "Updated N README badge(s)" / "bumped N README
    badge(s)" only when the count is truthy — printing those messages
    after a no-op write would mislead the operator into thinking the
    file mutated.

    Three branches all return ``0`` (no write, no message):

    * The README file does not exist (hand-edited, may be renamed).
    * The README exists but contains no ``?cacheSeconds=N&v=X.Y.Z``
      badges — regex matches nothing.
    * The README is already at ``new`` — regex matches, but the
      substituted bytes equal the originals (idempotent no-op).

    The only failure mode is permission / I/O against an existing
    file, which propagates as ``OSError`` to the caller.
    """
    if not file.exists():
        return 0
    text = file.read_text(encoding="utf-8")
    updated, count = update_readme_text(text, new)
    if count == 0 or updated == text:
        return 0
    file.write_text(updated, encoding="utf-8")
    return count


def resolve_target(
    current: Version,
    *,
    explicit: str | None,
    bump_kind: BumpKind | None,
) -> Version:
    """Compute the proposed new version from CLI arguments.

    Exactly one of ``explicit`` (a string ``X.Y.Z``) and ``bump_kind``
    (``major`` / ``minor`` / ``patch``) must be set. Raises
    :class:`ValueError` otherwise.

    The resulting version is validated to be strictly greater than
    ``current``; :class:`VersionNotGreaterError` is raised on a flat or
    backward jump (e.g. requesting ``0.1.0`` when current is already
    ``0.1.0``, or ``0.0.9`` when current is ``0.1.0``).
    """
    if (explicit is None) == (bump_kind is None):
        msg = (
            "exactly one of an explicit version (positional) or "
            "--major / --minor / --patch / --next must be supplied"
        )
        raise ValueError(msg)
    target = parse_version(explicit) if explicit is not None else current.bumped(bump_kind)  # type: ignore[arg-type]
    if not target > current:
        msg = (
            f"proposed version {target} is not strictly greater than "
            f"current {current} — a release must move forward"
        )
        raise VersionNotGreaterError(msg)
    return target


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns :data:`EXIT_OK` (0) on success."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "Bump __version__.",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help=(
            "Explicit canonical version X.Y.Z. Mutually exclusive with "
            "--major / --minor / --patch / --next."
        ),
    )
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument(
        "--major",
        action="store_const",
        const="major",
        dest="bump_kind",
        help="Bump the major component (X+1.0.0).",
    )
    bump_group.add_argument(
        "--minor",
        action="store_const",
        const="minor",
        dest="bump_kind",
        help="Bump the minor component (X.Y+1.0).",
    )
    bump_group.add_argument(
        "--patch",
        action="store_const",
        const="patch",
        dest="bump_kind",
        help="Bump the patch component (X.Y.Z+1).",
    )
    bump_group.add_argument(
        "--next",
        choices=BUMP_KINDS,
        dest="bump_kind",
        help="Alias for --major / --minor / --patch (matches scripts/release.py).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the bump without writing. Exits 0 when the bump is valid.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the current version and exit. No mutation.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=VERSION_FILE,
        help=f"Version file path (default: {VERSION_FILE.relative_to(_REPO_ROOT)}).",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=README_FILE,
        help=(
            "README path whose ``?cacheSeconds=N&v=X.Y.Z`` badge query "
            f"params are bumped alongside ``__version__`` (default: "
            f"{README_FILE.relative_to(_REPO_ROOT)}). Pass a non-existent "
            "path to skip the README rewrite (useful for tests)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        current = read_current(args.file)
    except (FileNotFoundError, VersionFormatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.print:
        print(current)
        return EXIT_OK

    try:
        target = resolve_target(
            current,
            explicit=args.version,
            bump_kind=args.bump_kind,
        )
    except VersionFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except VersionNotGreaterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_GREATER
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.check:
        print(f"OK: {current} -> {target} (no write; --check)")
        return EXIT_OK

    write_new(target, args.file)
    print(f"Bumped {current} -> {target} in {args.file}")
    readme_count = write_readme_badges(target, args.readme)
    if readme_count:
        print(f"Updated {readme_count} README badge(s) in {args.readme}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
