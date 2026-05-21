#!/usr/bin/env python3
"""Move ``CHANGELOG.md``'s ``## [Unreleased]`` entries into a new versioned section.

P4.c (2026-05-21) — second leg of the release-tooling triad. Reads
[`CHANGELOG.md`](../CHANGELOG.md), captures the body of the
``## [Unreleased]`` section, and rewrites the file with:

1. An empty placeholder ``## [Unreleased]`` section (so the operator
   has a clean slate to log the next release's entries under).
2. A new ``## [X.Y.Z] — YYYY-MM-DD`` section carrying the captured
   body verbatim.

Usage::

    python scripts/changelog.py 1.0.0
    python scripts/changelog.py 1.0.0 --date 2026-05-21
    python scripts/changelog.py 1.0.0 --check       # validate; no write
    python scripts/changelog.py 1.0.0 --allow-empty # finalise without unreleased content

Refuses to finalise an empty ``Unreleased`` section by default — the
operator should land release notes under ``Unreleased`` before
finalising. ``--allow-empty`` is the escape hatch for emergency
patches that ship without observable behaviour changes (the entry then
contains only the ``_No user-facing changes._`` sentinel line).

The script is intentionally narrow:

- It does **not** mutate ``__version__`` — that is
  ``scripts/bump_version.py``'s job.
- It does **not** create git commits or tags — that is
  ``scripts/release.py``'s job.
- It does **not** invent entries from git history — operators are
  expected to write release notes manually under ``Unreleased`` as PRs
  merge (per [`CHANGELOG.md`](../CHANGELOG.md)'s preamble).
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"

#: Header strings — kept as constants so the regex stays in sync with
#: the rewriter and the placeholder text never drifts.
UNRELEASED_HEADER = "## [Unreleased]"
PLACEHOLDER_BODY = "_No user-facing changes._"

#: Matches the ``## [Unreleased]`` header line and captures the body
#: until the next ``## `` heading (or end of file). The body capture is
#: greedy across newlines but stops at the first subsequent line
#: starting with ``## `` (any level-2 markdown heading).
_UNRELEASED_BLOCK_RE = re.compile(
    r"(?P<header>^## \[Unreleased\][^\n]*\n)(?P<body>.*?)(?=^## |\Z)",
    re.DOTALL | re.MULTILINE,
)

#: Strict ``X.Y.Z`` regex — mirrors bump_version's parser. Pre-release
#: + build-metadata suffixes are rejected so the release tag namespace
#: stays canonical.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: ISO-8601 calendar date pattern (``YYYY-MM-DD``).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: CLI exit codes.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_UNRELEASED = 3
EXIT_EMPTY_UNRELEASED = 4
EXIT_DUPLICATE_VERSION = 5


class ChangelogError(RuntimeError):
    """Base class for all changelog-finalisation errors."""


class NoUnreleasedSectionError(ChangelogError):
    """Raised when the changelog has no ``## [Unreleased]`` section to finalise."""


class EmptyUnreleasedSectionError(ChangelogError):
    """Raised when the ``## [Unreleased]`` section has no entries.

    The release captain is expected to land release notes under
    ``Unreleased`` before finalising. The ``--allow-empty`` flag is the
    escape hatch.
    """


class DuplicateVersionError(ChangelogError):
    """Raised when the target ``## [X.Y.Z]`` section already exists in the file.

    A duplicate version section would indicate a bug in the release
    pipeline (re-finalising the same version) or a hand-edit that
    landed the same version section before the script ran.
    """


def _has_entries(body: str) -> bool:
    """Return True when ``body`` carries any non-whitespace content.

    Whitespace-only bodies (the post-finalisation state after the
    previous release) count as "empty". A body containing only the
    ``PLACEHOLDER_BODY`` sentinel is treated as empty too — that's the
    sentinel ``--allow-empty`` writes; it must not be promoted as a
    real entry.
    """
    stripped = body.strip()
    if not stripped:
        return False
    return stripped != PLACEHOLDER_BODY


def _today_iso() -> str:
    """Return today's date in ``YYYY-MM-DD`` (UTC)."""
    return _dt.datetime.now(_dt.UTC).date().isoformat()


def finalize(
    text: str,
    *,
    version: str,
    date: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Return a new changelog text with ``Unreleased`` moved to a versioned section.

    Raises :class:`NoUnreleasedSectionError` when the input has no
    ``## [Unreleased]`` header, :class:`EmptyUnreleasedSectionError`
    when the section is empty and ``allow_empty`` is False, and
    :class:`DuplicateVersionError` when ``## [version]`` already
    exists.

    The version + date pair are validated against the canonical
    formats — ``ValueError`` is raised on malformed input.
    """
    if not _VERSION_RE.match(version):
        msg = f"version must be canonical X.Y.Z; got {version!r}"
        raise ValueError(msg)
    release_date = date if date is not None else _today_iso()
    if not _DATE_RE.match(release_date):
        msg = f"date must be ISO-8601 YYYY-MM-DD; got {release_date!r}"
        raise ValueError(msg)

    if f"## [{version}]" in text:
        msg = f"a ``## [{version}]`` section already exists in the changelog"
        raise DuplicateVersionError(msg)

    match = _UNRELEASED_BLOCK_RE.search(text)
    if match is None:
        msg = "no ``## [Unreleased]`` section found in the changelog"
        raise NoUnreleasedSectionError(msg)

    body = match.group("body")
    if not allow_empty and not _has_entries(body):
        msg = (
            "``## [Unreleased]`` has no entries — refuse to finalise. "
            "Land release notes under Unreleased first, or pass "
            "``--allow-empty`` for an emergency-patch release with "
            "no user-facing changes."
        )
        raise EmptyUnreleasedSectionError(msg)

    if allow_empty and not _has_entries(body):
        body_to_version = f"\n{PLACEHOLDER_BODY}\n\n"
    else:
        # Preserve the captured body byte-for-byte, but normalise the
        # trailing whitespace so the rendered file has exactly one
        # blank line before the next ``## `` header.
        body_to_version = body.rstrip() + "\n\n"

    new_unreleased = f"{UNRELEASED_HEADER}\n\n"
    new_version_header = f"## [{version}] — {release_date}\n"
    new_block = (
        f"{new_unreleased}"  # placeholder Unreleased (intentionally empty)
        f"{new_version_header}"  # versioned header
        f"{body_to_version}"  # the captured body
    )
    return text[: match.start()] + new_block + text[match.end() :]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns :data:`EXIT_OK` (0) on success."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "Finalise CHANGELOG Unreleased.",
    )
    parser.add_argument(
        "version",
        help="Canonical X.Y.Z to promote ``[Unreleased]`` into.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Release date in ISO-8601 (YYYY-MM-DD). Defaults to today (UTC).",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Finalise even when ``Unreleased`` has no entries. The new section "
            f"will carry only ``{PLACEHOLDER_BODY}``."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate without writing. Exits 0 when the changelog is ready to finalise.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=CHANGELOG_PATH,
        help=f"Changelog file path (default: {CHANGELOG_PATH.relative_to(_REPO_ROOT)}).",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        updated = finalize(
            text,
            version=args.version,
            date=args.date,
            allow_empty=args.allow_empty,
        )
    except ValueError as exc:  # malformed inputs
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NoUnreleasedSectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_UNRELEASED
    except EmptyUnreleasedSectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_EMPTY_UNRELEASED
    except DuplicateVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DUPLICATE_VERSION

    if args.check:
        print(f"OK: changelog ready to finalise as {args.version} (--check)")
        return EXIT_OK

    args.file.write_text(updated, encoding="utf-8")
    release_date = args.date if args.date is not None else _today_iso()
    print(f"Finalised CHANGELOG: [Unreleased] -> [{args.version}] — {release_date}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
