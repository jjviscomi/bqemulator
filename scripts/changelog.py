#!/usr/bin/env python3
"""Validate and date-stamp a release-time CHANGELOG section.

The release operator authors a new ``## [X.Y.Z]`` section under the
preamble before invoking the release flow, populating
``### Changed`` / ``### Added`` / ``### Removed`` / ``### Fixed``
bullets synthesised from ``git log <prev-tag>..HEAD``. This script
validates the section exists, has body content, matches the target
version, and stamps today's release date into the header.

The script does not invent entries from git history — synthesis is
the operator's job. See
[`docs/architecture/contributing/documentation-style-guide.md`](../docs/architecture/contributing/documentation-style-guide.md)
for the entry-form rules.

Usage::

    python scripts/changelog.py 1.0.0
    python scripts/changelog.py 1.0.0 --date 2026-05-21
    python scripts/changelog.py 1.0.0 --check       # validate; no write

Idempotent: a section already stamped with the supplied date is a
no-op; an existing date is overwritten with the supplied one so
retries after a failed release converge.
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"

#: Matches the first ``## [X.Y.Z]`` (optionally followed by ``- YYYY-MM-DD``)
#: heading after the preamble, capturing the version, an optional date
#: suffix, and the body until the next ``## `` heading or end of file.
_SECTION_BLOCK_RE = re.compile(
    r"(?P<header>^## \[(?P<version>\d+\.\d+\.\d+)\]"
    r"(?:[ \t]*-[ \t]*(?P<date>\d{4}-\d{2}-\d{2}))?[^\n]*\n)"
    r"(?P<body>.*?)(?=^## |\Z)",
    re.DOTALL | re.MULTILINE,
)

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_SECTION = 3
EXIT_EMPTY_SECTION = 4
EXIT_VERSION_MISMATCH = 5


class ChangelogError(RuntimeError):
    """Base class for changelog validation errors."""


class NoSectionError(ChangelogError):
    """Raised when no ``## [X.Y.Z]`` section is present at the top of the file."""


class EmptySectionError(ChangelogError):
    """Raised when the target section has no bullet entries."""


class VersionMismatchError(ChangelogError):
    """Raised when the topmost section's version does not match the release target."""


def _has_entries(body: str) -> bool:
    """Return True when the section body contains at least one bullet line."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            return True
    return False


def _today_iso() -> str:
    """Return today's date in ``YYYY-MM-DD`` (UTC)."""
    return _dt.datetime.now(_dt.UTC).date().isoformat()


def stamp(
    text: str,
    *,
    version: str,
    date: str | None = None,
) -> str:
    """Return a new changelog with the topmost section's date stamped.

    Validates that the topmost ``## [X.Y.Z]`` section matches
    ``version`` and contains bullet entries. If the section header
    carries no date, stamps the supplied (or today's) date. If it
    already carries a date, the date is overwritten — retries
    converge.

    Raises :class:`NoSectionError` when no versioned section exists,
    :class:`EmptySectionError` when the section has no bullet
    entries, :class:`VersionMismatchError` when the topmost
    section's version is not ``version``. Raises ``ValueError`` on
    malformed version or date arguments.
    """
    if not _VERSION_RE.match(version):
        msg = f"version must be canonical X.Y.Z; got {version!r}"
        raise ValueError(msg)
    release_date = date if date is not None else _today_iso()
    if not _DATE_RE.match(release_date):
        msg = f"date must be ISO-8601 YYYY-MM-DD; got {release_date!r}"
        raise ValueError(msg)

    match = _SECTION_BLOCK_RE.search(text)
    if match is None:
        msg = "no ``## [X.Y.Z]`` section found in the changelog"
        raise NoSectionError(msg)

    found_version = match.group("version")
    if found_version != version:
        msg = (
            f"topmost changelog section is ``## [{found_version}]`` "
            f"but the release target is ``{version}``. Prepend a "
            f"``## [{version}]`` section under the preamble before "
            "running the release flow."
        )
        raise VersionMismatchError(msg)

    body = match.group("body")
    if not _has_entries(body):
        msg = (
            f"``## [{version}]`` section has no bullet entries. "
            "Populate ``### Changed`` / ``### Added`` / ``### Removed`` "
            "/ ``### Fixed`` bullets before running the release flow."
        )
        raise EmptySectionError(msg)

    new_header = f"## [{version}] - {release_date}\n"
    return text[: match.start("header")] + new_header + text[match.end("header") :]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns :data:`EXIT_OK` (0) on success."""
    parser = argparse.ArgumentParser(
        description="Validate and date-stamp a release-time CHANGELOG section.",
    )
    parser.add_argument(
        "version",
        help="Canonical X.Y.Z of the release section to stamp.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Release date in ISO-8601 (YYYY-MM-DD). Defaults to today (UTC).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate without writing. Exits 0 when the section is well-formed.",
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
        updated = stamp(text, version=args.version, date=args.date)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NoSectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_SECTION
    except EmptySectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_EMPTY_SECTION
    except VersionMismatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VERSION_MISMATCH

    if args.check:
        print(f"OK: CHANGELOG section [{args.version}] is well-formed (--check)")
        return EXIT_OK

    args.file.write_text(updated, encoding="utf-8")
    release_date = args.date if args.date is not None else _today_iso()
    print(f"Stamped CHANGELOG: [{args.version}] - {release_date}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
