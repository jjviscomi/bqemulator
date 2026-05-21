#!/usr/bin/env python3
"""Normalise a pytest-benchmark autosave JSON to the committed baseline schema.

pytest-benchmark's autosave format embeds machine-info, CPU details,
Python build hash, and per-round timing arrays — none of which the
Tier 6 comparison gate uses. The committed
``tests/perf/baselines/<arch>.json`` keeps only the fields the
comparison gate reads: name, median, stddev, rounds, unit.

This script reads a pytest-benchmark JSON and writes the trimmed
shape documented in
[`ADR 0025 §6`](../docs/adr/0025-perf-tier-design-contract.md).

Usage::

    python scripts/normalize_perf_baseline.py \\
        .benchmarks/Linux-CPython-3.11-64bit/0001_baseline.json \\
        tests/perf/baselines/linux-x86_64.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
import sys


def normalise(source_path: Path, arch: str) -> dict[str, object]:
    """Return a normalised baseline dict for the given pytest-benchmark JSON."""
    raw = json.loads(source_path.read_text())
    benchmarks: list[dict[str, object]] = []
    for entry in raw.get("benchmarks", []):
        stats = entry.get("stats", {})
        # ``fullname`` is the canonical pytest test id (e.g.
        # ``tests/perf/test_query_latency.py::test_tpch_query_latency[Q1]``).
        # The committed baseline keys on this value so a re-record
        # against the same test ids stays diff-comprehensible.
        benchmarks.append(
            {
                "name": str(entry.get("fullname", entry.get("name", ""))),
                "median": float(stats.get("median", 0.0)),
                "stddev": float(stats.get("stddev", 0.0)),
                "rounds": int(stats.get("rounds", 0)),
                "unit": str(entry.get("options", {}).get("unit", "seconds")),
            },
        )
    return {
        "version": 1,
        "arch": arch,
        "recorded_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "benchmarks": benchmarks,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pytest-benchmark autosave JSON")
    parser.add_argument("dest", type=Path, help="target tests/perf/baselines/<arch>.json")
    args = parser.parse_args(argv)

    arch = args.dest.stem  # filename without .json
    payload = normalise(args.source, arch)
    args.dest.parent.mkdir(parents=True, exist_ok=True)
    args.dest.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {len(payload['benchmarks'])} benchmarks to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
