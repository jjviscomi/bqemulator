# ADR 0025: Performance-tier (Tier 6) design contract

- **Status**: Accepted

## Context

The
[`docs/architecture/testing-strategy.md`](../architecture/testing-strategy.md)
document defines a seven-tier testing pyramid. Tiers 1-5 + Tier 7
have shipped; **Tier 6 (performance) has been empty since the
pyramid was first documented in Phase 11 slice 1.** The
Phase 11 roadmap doc lists "no
performance benchmark has regressed > 10% vs the Phase 10 baseline"
as a v1.0 ship criterion — but no baseline was ever recorded, so the
criterion was inherently unenforceable.

Two operational requirements distinguish a performance benchmark
from the other six tiers:

1. **A benchmark with no baseline is meaningless.** A green
   "1.4 s per query" line tells the operator nothing — only its
   delta from a previously-recorded value does. Tier 6 is the only
   tier whose unit of analysis is a *comparison*, not an
   *assertion*.
2. **Hardware variation dominates a wall-clock threshold.** A query
   that runs in 80 ms on an Apple M2 may run in 140 ms on a GitHub
   Actions x86_64 runner; both are correct relative to their own
   baseline. A single "absolute threshold" gate would flake
   spuriously across CI / dev environments without buying any new
   regression coverage.

ADR 0021 captures the contract for Tier 7 (chaos); this ADR captures
the analogous contract for Tier 6 (performance). The five-scenario
split was set by the Phase 11 doc's "Performance benchmarks"
subsection; this ADR locks in the *rules* every scenario must
honour.

## Decisions

### 1. Performance is Tier 6 of a 7-tier pyramid

The [testing-strategy](../architecture/testing-strategy.md) document
defines seven tiers. Performance sits below chaos (Tier 7) because
it runs on every commit, but above e2e (Tier 4) because it
*compares* against a stored baseline rather than asserting an
intrinsic invariant. ``make test-perf`` runs it locally; the
[`perf.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/perf.yml)
workflow runs it on every commit in CI.

The five scenarios cover the user-facing bottlenecks an operator
hits in production:

| File | Scenario | Metric |
|---|---|---|
| ``test_cold_start.py`` | ``docker run`` → first ``/healthz`` 200 | p50 / p95 latency |
| ``test_query_latency.py`` | TPC-H SF0.01 Q1/Q3/Q5/Q6/Q10 | p50 / p95 / p99 |
| ``test_storage_read_throughput.py`` | Arrow IPC via ``CreateReadSession`` → ``ReadRows`` on a 100 K-row table (Avro skipped — not yet implemented per the [compatibility matrix](../reference/compatibility-matrix.md)) | MiB/s |
| ``test_insert_all_throughput.py`` | REST ``tabledata.insertAll`` batches of 1 / 10 / 100 / 1 000 | rows/s |
| ``test_storage_write_throughput.py`` | All 4 stream types × 2 input formats | rows/s |

### 2. Baselines are stored per-arch and compared relative

Each baseline file lives at
[`tests/perf/baselines/<arch>.json`](https://github.com/jjviscomi/bqemulator/blob/main/tests/perf/baselines/)
where ``<arch>`` is one of:

| Arch | Used for |
|---|---|
| ``linux-x86_64`` | CI canonical |
| ``linux-arm64`` | CI ARM runner |
| ``darwin-arm64`` | dev-box local runs |

The arch is auto-detected from ``platform.machine()`` +
``sys.platform``. CI always compares against ``linux-x86_64``; local
runs compare against the host arch (falling back to the canonical
baseline with a warning if the host arch has no recorded baseline
yet).

**The 10% regression gate is per-scenario, not aggregate.** A 9.9%
regression on every scenario does not pass — each must stay within
its own 10% bound. ``pytest-benchmark``'s ``--benchmark-compare-fail``
flag enforces the bound on every commit.

### 3. Baselines are deliberately committed, never autosaved

``pytest-benchmark`` ships with both ``--benchmark-autosave`` (writes
a timestamped JSON every run) and ``--benchmark-save=<name>`` (writes
to a stable name the operator chose). Tier 6 uses the latter, never
the former. The CI workflow runs with neither flag — it only
*compares* against the committed baseline.

A baseline update is a deliberate operator action:

```bash
# Run 5+ times to compute a stable median, write to a stable name.
pytest tests/perf -m perf --benchmark-save=linux-x86_64
# Review tests/perf/baselines/linux-x86_64.json, commit, open a PR.
```

The forcing function mirrors recording conformance fixtures: a
baseline drift is a code change that lands through review, not an
automated diff in CI.

The Makefile target
[`make test-perf`](https://github.com/jjviscomi/bqemulator/blob/main/Makefile)
runs the suite *with* the comparison gate (failing on >10%
regression) but does NOT save a new baseline — a recording requires
explicit ``--benchmark-save`` invocation.

### 4. The five-scenario split is exhaustive for v1.0

Adding a sixth scenario means adding a new file plus baseline
entries for every recorded arch. The five-scenario split was chosen
to cover the user-visible bottleneck classes without growing into a
maintenance liability:

- Cold start (container startup) — bounds the worst-case latency a
  CI pipeline sees on a fresh runner.
- Query latency (SQL execution) — covers the analytical-workload
  hot path.
- Storage Read throughput — covers the bulk-read hot path used by
  client libraries' ``query → result rows`` streaming.
- ``insertAll`` throughput — covers the REST streaming-insert path
  used by every non-Storage-Write writer.
- Storage Write throughput — covers the gRPC streaming-insert path
  introduced in Phase 5.

The five categories collectively touch every transport boundary
(REST + gRPC), every storage path (read + write), and every
language client's hot path (clients use one or more of the five for
~all production traffic).

### 5. Determinism: fixed dataset, fixed seed, no networking inside the loop

Each benchmark constructs a fixed dataset *before* the timed block
starts, then timing measures only the in-process work. The Storage
Read / Write benchmarks use the in-process emulator endpoint on
``127.0.0.1`` (no Docker, no loopback gRPC over the wire); the
cold-start benchmark is the *only* scenario that spans a process
boundary, and it is timed end-to-end deliberately because that *is*
the metric the operator cares about.

The fixed dataset (`tests/perf/_fixtures.py`) is the same
1 000 / 10 000 / 100 000-row table for every run. Random data is
seeded with the chaos-tier convention (``BQEMU_PERF_SEED``, default
``0``).

### 6. Per-arch baseline files use a flat JSON schema

Every baseline file looks like:

```json
{
  "version": 1,
  "arch": "linux-x86_64",
  "recorded_at": "2024-05-19T12:34:56Z",
  "benchmarks": [
    {
      "name": "test_cold_start::test_cold_start_to_healthz",
      "median": 4.213,
      "stddev": 0.150,
      "rounds": 5,
      "unit": "s"
    },
    ...
  ]
}
```

This is a thin compatibility shim over ``pytest-benchmark``'s
machine-info-laden native JSON; the [`conftest.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/perf/conftest.py)
loader extracts just the relevant median / stddev / unit fields so a
baseline survives a ``pytest-benchmark`` version bump.

### 7. Cold-start runs once per session, not per-round

``pytest-benchmark`` defaults to ``--benchmark-min-rounds=5``; for
every benchmark *except* cold-start, this gives a useful median +
stddev. Cold-start is intentionally slow (~5 s including image
load), so requiring 5 rounds would balloon the CI runtime by 25 s
per CI minute saved elsewhere. Cold-start declares
``@pytest.mark.benchmark(min_rounds=3, max_time=60)`` to keep the
wall-clock budget bounded.

The other four scenarios use the default 5+ rounds.

## Consequences

- **Positive.** The v1.0 ship-criterion gate "no performance
  benchmark has regressed > 10% vs the Phase 10 baseline" is now
  enforceable. Phase 10's "baseline" was aspirational; this session
  *recorded* the baseline (under the label "v1.0-rc baseline" —
  *not* retroactively dated Phase 10) and wired the comparison
  into every commit.

- **Positive.** Per-arch baselines mean a CI ARM runner and a
  developer's M2 macbook both have stable regression coverage
  without the cross-arch noise that a single absolute threshold
  would introduce.

- **Positive.** The "baselines are deliberate, never autosaved"
  rule mirrors the existing forcing function for conformance
  fixtures — operators already know how the recording workflow
  feels.

- **Negative.** A baseline drift over time (e.g. DuckDB gets faster
  by 30%) requires a manual re-record; otherwise CI passes but the
  baseline is no longer meaningful. The mitigation is a quarterly
  "baseline freshness" review during the release-readiness audit,
  which the release-tooling workstream P4.c can codify if it
  becomes a recurring concern.

- **Negative.** Cold-start adds a Docker dependency to the perf
  tier. CI runners already have Docker (e2e tier uses it); local
  developers who run ``make test-perf`` without Docker get a
  ``pytest.skip`` on that one scenario, mirroring the chaos tier's
  spatial-extension-offline skip pattern.

- **Negative.** Bench results have inherent noise (Python startup
  jitter, OS scheduling, DuckDB query-plan caching). The 10%
  threshold absorbs typical noise but a truly flaky benchmark will
  surface as a CI flake. The mitigation is the ``min_rounds=5``
  default + a per-scenario stddev report in the benchmark output —
  a flake shows as a high-stddev row in the summary table and gets
  triaged like any other test flake.

## Implementation notes

- The ``perf`` pytest marker was already registered in
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
  by Phase 11 slice 1 (it was reserved for this tier).
- ``pytest-benchmark>=4.0`` is in the ``testing`` extra.
- The ``make test-perf`` target invokes the comparison gate; it
  does NOT save baselines. Baseline recording is a separate
  invocation documented in the operator guide.
- The cold-start scenario uses ``docker run`` + ``curl`` polling
  on ``/healthz``; it ``pytest.skip``s with a documented reason
  when the Docker daemon is unreachable.
- Storage Read / Write benchmarks reuse the
  [`bqemu_server`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/testing/fixtures.py)
  session fixture (in-process emulator); the benchmark loop opens
  a fresh gRPC channel per round so per-round timing is honest.
- The ``perf.yml`` workflow runs on every PR + push to ``main`` as
  a required-status check. It does not block release tagging
  directly — the v1.0 release-readiness gate reads the same
  status.

## References

- [Tier 6 in the testing-strategy doc](../architecture/testing-strategy.md)
- Phase 11 roadmap doc — performance benchmarks section
- [ADR 0021](0021-chaos-tier-design-contract.md) — Tier 7 design
  contract, structurally analogous to this ADR
- v1-confidence-plan workstream P3.b
  — this ADR closes the workstream
