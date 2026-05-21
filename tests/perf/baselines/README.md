# Performance baselines

Per-arch baseline files for the Tier 6 performance suite. See
[`ADR 0025`](../../../docs/adr/0025-perf-tier-design-contract.md) for the
design contract.

## Files

| File | Arch | Used for |
|---|---|---|
| `linux-x86_64.json` | x86_64 Linux | CI canonical (always compared in GitHub Actions) |
| `linux-arm64.json` | arm64 Linux | CI ARM runner (optional) |
| `darwin-arm64.json` | arm64 macOS (Apple Silicon) | dev-box local runs |

The arch is auto-detected by [`conftest.py`](../conftest.py) from
`platform.machine()` + `sys.platform`. A host arch without a recorded
baseline falls back to the CI canonical (`linux-x86_64`) so the
comparison still surfaces drift even if absolute numbers shift.

## Schema

```json
{
  "version": 1,
  "arch": "linux-x86_64",
  "recorded_at": "2026-05-19T12:34:56Z",
  "benchmarks": [
    {
      "name": "test_cold_start::test_cold_start_to_healthz",
      "median": 4.213,
      "stddev": 0.150,
      "rounds": 5,
      "unit": "s"
    }
  ]
}
```

Every entry is keyed by the fully-qualified pytest name and maps to a
`{median, stddev, rounds, unit}` quad. The shim is intentionally
thinner than `pytest-benchmark`'s native autosave format so a
baseline survives a `pytest-benchmark` version bump.

## Recording

A baseline update is a deliberate operator action, mirroring the
forcing function for conformance fixtures:

```bash
# Run 5+ rounds, write to a stable name.
pytest tests/perf -m perf --benchmark-save=linux-x86_64

# Convert the pytest-benchmark autosave shape to the committed
# baseline schema (helper script lives at
# scripts/normalize_perf_baseline.py).
python scripts/normalize_perf_baseline.py \
    .benchmarks/Linux-CPython-3.11-64bit/<latest>.json \
    tests/perf/baselines/linux-x86_64.json

# Review the diff, commit, open a PR.
```

The Makefile's [`make test-perf`](../../../Makefile) target runs the
comparison gate (fails on >10% regression) but does NOT save a new
baseline — recording requires the explicit `--benchmark-save`
invocation.
