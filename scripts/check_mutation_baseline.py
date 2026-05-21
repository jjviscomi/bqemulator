#!/usr/bin/env python3
"""Compare a fresh mutmut run against the committed mutation baseline.

Reads ``mutants/mutmut-cicd-stats.json`` (produced by
``mutmut export-cicd-stats``) and compares the live mutation score to
the committed ``tests/mutation/baseline.json``. Exits non-zero when the
live score drops more than ``--max-regression`` points (default 2.0)
below the baseline — the mutation-tier ship criterion documented in
[ADR 0026](../docs/adr/0026-mutation-tier-design-contract.md) and the
v1 confidence plan.

Score = killed / (killed + survived). ``no_tests`` mutants (no test
exercises the line at all) and ``skipped`` mutants are excluded from
the denominator because they reflect coverage-tier gaps, not
test-tier weakness — counting them would inflate or deflate the score
on coverage churn alone.

Usage::

    python scripts/check_mutation_baseline.py
    python scripts/check_mutation_baseline.py --max-regression 2.0
    python scripts/check_mutation_baseline.py --update-baseline
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_STATS_PATH = REPO_ROOT / "mutants" / "mutmut-cicd-stats.json"
BASELINE_PATH = REPO_ROOT / "tests" / "mutation" / "baseline.json"


class BaselineMissingError(RuntimeError):
    """Raised when the committed baseline file is absent."""


class LiveStatsMissingError(RuntimeError):
    """Raised when ``mutmut export-cicd-stats`` has not been invoked."""


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        msg = f"{path} does not exist"
        raise FileNotFoundError(msg)
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _coerce_int(value: object, *, default: int = 0) -> int:
    """Return ``value`` as an int, falling back to ``default`` on missing keys.

    mutmut's cicd-stats JSON stores every count as a JSON number, but a
    typed ``dict.get`` returns ``object`` to mypy. This helper narrows
    the type while keeping mypy strict mode happy.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        # bool is a subclass of int but never appears as a mutmut count;
        # reject explicitly so a config error surfaces here rather than
        # silently truthing into a count of 1.
        msg = f"expected int, got bool: {value!r}"
        raise TypeError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        return int(value)
    msg = f"cannot coerce {value!r} (type {type(value).__name__}) to int"
    raise TypeError(msg)


def compute_score(killed: int, survived: int) -> float:
    """Return killed / (killed + survived) as a percentage.

    Returns 0.0 when there are no scored mutants (avoids div-by-zero
    on a fresh checkout where mutmut has not run yet).
    """
    denominator = killed + survived
    if denominator == 0:
        return 0.0
    return round(100.0 * killed / denominator, 2)


def load_live_stats(path: Path = LIVE_STATS_PATH) -> dict[str, int]:
    """Read mutmut's cicd-stats JSON and return the killed/survived/total view."""
    if not path.exists():
        msg = (
            f"{path} not found. Run `make test-mutation` (or "
            "`mutmut run && mutmut export-cicd-stats`) before invoking "
            "this script."
        )
        raise LiveStatsMissingError(msg)
    raw = _read_json(path)
    killed = _coerce_int(raw.get("killed"))
    survived = _coerce_int(raw.get("survived"))
    no_tests = _coerce_int(raw.get("no_tests"))
    skipped = _coerce_int(raw.get("skipped"))
    suspicious = _coerce_int(raw.get("suspicious"))
    timeout = _coerce_int(raw.get("timeout"))
    total = _coerce_int(
        raw.get("total"),
        default=killed + survived + no_tests + skipped,
    )
    return {
        "killed": killed,
        "survived": survived,
        "no_tests": no_tests,
        "skipped": skipped,
        "suspicious": suspicious,
        "timeout": timeout,
        "total": total,
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, object]:
    """Read the committed baseline JSON."""
    if not path.exists():
        msg = (
            f"{path} not found. Bootstrap one with "
            "`python scripts/check_mutation_baseline.py --update-baseline`."
        )
        raise BaselineMissingError(msg)
    return _read_json(path)


def write_baseline(live: dict[str, int], path: Path = BASELINE_PATH) -> dict[str, object]:
    """Write a new baseline JSON from the supplied live stats.

    A re-record clears the seed marker (``pending_first_ci_run``) and the
    explanatory ``_comment`` so the file is a clean canonical baseline.
    """
    score = compute_score(live["killed"], live["survived"])
    payload: dict[str, object] = {
        "score": score,
        "killed": live["killed"],
        "survived": live["survived"],
        "no_tests": live["no_tests"],
        "skipped": live["skipped"],
        "timeout": live["timeout"],
        "suspicious": live["suspicious"],
        "total": live["total"],
        "run_at": _dt.datetime.now(_dt.UTC).date().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def check_regression(
    live: dict[str, int],
    baseline: dict[str, object],
    *,
    max_regression: float,
) -> tuple[bool, float, float]:
    """Return (ok, live_score, baseline_score).

    ``ok`` is True when the live score is no more than ``max_regression``
    points below the baseline score.
    """
    live_score = compute_score(live["killed"], live["survived"])
    baseline_score = float(baseline["score"])  # type: ignore[arg-type]
    drop = baseline_score - live_score
    return drop <= max_regression, live_score, baseline_score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-regression",
        type=float,
        default=2.0,
        help="Maximum allowed drop in score (percentage points). Default 2.0.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the committed baseline with the live stats. Use sparingly.",
    )
    args = parser.parse_args(argv)

    try:
        live = load_live_stats()
    except LiveStatsMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        payload = write_baseline(live)
        print(f"Updated {BASELINE_PATH} to:")
        print(json.dumps(payload, indent=2))
        return 0

    try:
        baseline = load_baseline()
    except BaselineMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if baseline.get("pending_first_ci_run"):
        print(
            "Baseline is a seed (pending_first_ci_run=true). Skipping the "
            "regression gate; dispatch `.github/workflows/mutation.yml` with "
            "`record-baseline=true` to land a real baseline.",
        )
        return 0

    ok, live_score, baseline_score = check_regression(
        live,
        baseline,
        max_regression=args.max_regression,
    )
    drop = baseline_score - live_score
    if ok:
        print(
            f"Mutation score {live_score:.2f}% "
            f"(baseline {baseline_score:.2f}%, drop {drop:+.2f}pp; "
            f"max allowed {args.max_regression:.2f}pp).",
        )
        return 0

    print(
        f"REGRESSION: mutation score {live_score:.2f}% is {drop:.2f}pp below "
        f"the baseline {baseline_score:.2f}% (max allowed {args.max_regression:.2f}pp).",
        file=sys.stderr,
    )
    print(
        "Re-baseline with `make test-mutation` + "
        "`python scripts/check_mutation_baseline.py --update-baseline` "
        "after the tests catch up.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
