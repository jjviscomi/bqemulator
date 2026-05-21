"""Performance tier (Tier 6) — pytest-benchmark scenarios with stored baselines.

Every test in this package measures a user-visible bottleneck class
(cold start, query latency, Storage Read throughput, ``insertAll``
throughput, Storage Write throughput) against a per-arch baseline
stored in :mod:`tests.perf.baselines`. The design contract is in
:doc:`/adr/0025-perf-tier-design-contract`; the rules are summarised
in :doc:`/architecture/testing-strategy` (Tier 6).

Rules (enforced by the runner + the tests themselves):

* **Comparison, not assertion.** Every scenario is meaningful only
  relative to its committed baseline. ``pytest-benchmark``'s
  ``--benchmark-compare-fail`` flag enforces the 10% regression
  threshold on every commit.
* **Per-arch baselines.** Baselines live at
  ``tests/perf/baselines/<arch>.json`` where ``<arch>`` is one of
  ``linux-x86_64`` / ``linux-arm64`` / ``darwin-arm64``. CI compares
  against ``linux-x86_64``; local runs auto-detect.
* **Baselines are deliberately recorded, never autosaved.**
  ``make test-perf`` runs the comparison gate but does not save a
  new baseline; recording requires explicit
  ``pytest tests/perf --benchmark-save=<arch>``.
* **Determinism inside the timed block.** Random data is built
  outside the benchmark callable; the loop measures only the
  in-process work.

Performance tests run on **every commit** in CI (a separate workflow
from the standard tier) and may also be invoked locally via
``make test-perf``. The 10% threshold is per-scenario, not
aggregate — a 9.9% regression on every scenario does not pass.
"""
