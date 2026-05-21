#!/usr/bin/env python3
"""Focus the conformance corpus on a single ADR-0023 divergence bucket.

Reads :data:`tests.conformance.divergences.KNOWN_DIVERGENCES`,
filters to the entries whose rationale matches ``Bucket <X>``, and
runs ``pytest`` with a ``-k`` selector limited to those fixtures.

Intended for parity-closure work: when fixing the source-side issue
behind a bucket, you run::

    python scripts/run_bucket.py A

…and pytest only invokes the ~22 Bucket-A fixtures. A successful fix
flips them from XFAIL to XPASS — which under ``strict=True`` fails
the suite, forcing the closure PR to also remove the corresponding
entries from ``divergences.py``.

The helper does **not** modify ``divergences.py`` — removal is a
deliberate manual step in the closure PR.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conformance.divergences import KNOWN_DIVERGENCES  # noqa: E402

_VALID_BUCKETS = {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "spheroidal"}


def _bucket_token(letter: str) -> str:
    if letter == "spheroidal":
        return "Spheroidal-vs-planar"
    return f"Bucket {letter} "


def _select_fixtures(letter: str) -> list[str]:
    token = _bucket_token(letter)
    return sorted(fid for fid, reason in KNOWN_DIVERGENCES.items() if token in reason)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "bucket",
        choices=sorted(_VALID_BUCKETS),
        help="Bucket letter (A..J) or 'spheroidal' for the GEOGRAPHY entries.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print fixture ids without running pytest.",
    )
    # Use parse_known_args so anything we don't recognise is
    # forwarded verbatim to pytest. The `--` convention still works
    # — argparse just drops it into the unknown list.
    args, extra = parser.parse_known_args(argv)

    fixtures = _select_fixtures(args.bucket)
    if not fixtures:
        print(
            f"No fixtures matched bucket {args.bucket!r}. Check tests/conformance/divergences.py.",
            file=sys.stderr,
        )
        return 1

    print(f"Bucket {args.bucket}: {len(fixtures)} fixtures")
    for fid in fixtures:
        print(f"  {fid}")

    if args.list:
        return 0

    # The parametrize ids include '/' (e.g.
    # "rest_crud/select_avg"); pytest's -k matches against
    # the full nodeid as a substring so we can join the names with
    # 'or' to select exactly this set.
    expr = " or ".join(fixtures)
    # Drop a leading '--' separator if the caller used one.
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd = [
        "pytest",
        "tests/conformance",
        "-m",
        "conformance",
        "-k",
        expr,
        "-v",
        *extra,
    ]
    summary_cmd = f"{' '.join(cmd[:5])} -k '<{len(fixtures)} ids>' {' '.join(extra)}"
    print(f"\nRunning: {summary_cmd}\n")
    # The pytest invocation is constructed entirely from this
    # script's known argv plus the curated fixture ids from
    # divergences.py — no shell, no untrusted input path.
    return subprocess.call(cmd, cwd=_REPO_ROOT)  # noqa: S603


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
