# ADR 0021: Chaos-tier (Tier 7) design contract

- **Status**: Accepted

## Context

Phase 10's production-readiness audit surfaced eight failure modes
that no existing test tier reliably covered: process crashes mid-DDL
and mid-stream, concurrent stale-readers on materialized views, retry
storms at 1000+ threads, resource exhaustion (disk-full, FD-cap,
buffer-cap), DuckDB file-lock contention across processes, forward-
only migration partial-apply recovery, the spatial-extension offline
startup path, and gRPC stream cancellation under back-pressure.

These failure modes share three properties:

1. They are real production hazards an operator will see at some
   point.
2. They are not naturally reproducible from a unit or integration
   test — they need fault injection or process-level orchestration.
3. They are slower than the per-commit gate tolerates: a single
   subprocess-kill scenario takes seconds, not the milliseconds the
   unit/property/integration tiers target.

We need a dedicated test tier that owns these scenarios with rules
that distinguish a meaningful chaos test from a flaky one. ADR 0017
documented the MV refresh model that one of these scenarios validates
under contention; ADR 0013 documented the Write API's exactly-once
semantics that the retry-storm scenario property-tests. Neither
contract was actually exercised under real contention until Phase 11
shipped the chaos tier.

This ADR captures the contract every chaos test in
[`tests/chaos/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/chaos/) must honour.

## Decisions

### 1. Chaos is Tier 7 of a 7-tier pyramid

The [testing-strategy](../architecture/testing-strategy.md) document
defines seven tiers. Chaos sits at the top — slower than the gates
run on every commit, but mandatory before a release. Chaos runs
**nightly** in CI; ``make test-chaos`` runs it locally.

Tiers 1-4 + 6 (unit, property, integration, e2e, perf) run on every
commit. Tier 5 (conformance) and Tier 7 (chaos) run nightly.

### 2. Five categories, one file per category

The Phase 11 audit mapped each open gap to one of five categories.
Adding a new chaos scenario means picking the category it belongs to
and adding it to the existing file:

| File | Category | Audit gap |
|---|---|---|
| ``test_concurrency.py`` | Concurrent races | #5 (MV refresh) + extends #4 |
| ``test_resource_exhaustion.py`` | Resource pressure | #6 |
| ``test_crash_recovery.py`` | Process-level crash | #2 process-side |
| ``test_storage_failures.py`` | Storage primitives | #7, #9, #10 |
| ``test_network_failures.py`` | gRPC / REST boundary | #2 network-side |

Six categories would have been a defensible split (e.g. "lock
contention" separated from "concurrent races"); five matches the
audit's natural fault-mode taxonomy and keeps the surface manageable.

### 3. Deterministic given a seed

Every chaos test must reproduce the same interleaving on every run
given the same seed. Three implementation strategies, in order of
preference:

1. **No randomness at all.** A `threading.Barrier` to release N
   threads simultaneously, an `asyncio.Barrier` for async scenarios —
   the interleaving is determined by the lock + scheduling
   primitives, not by chance.
2. **Fixed seed for RNG-driven inputs.** The chaos conftest seeds
   `random` at import. Tests may consume the RNG freely; the
   ``BQEMU_CHAOS_SEED`` env var overrides the default (0). The seed
   is printed in pytest's session header so a CI flake can be
   reproduced by copying the seed line.
3. **Subprocess-based scenarios** publish a ``READY`` sentinel on
   stdout before the parent injects the fault. The parent reads the
   pipe (not a sleep) to know the child has reached the right state.

What we explicitly **reject**:

- ``time.sleep(0.1)`` as a "let the other thread get there" pattern.
- ``random.random() < 0.5`` to choose which fault to inject.
- ``hypothesis`` settings that vary across runs without a stored seed.

A chaos test that "usually" passes is a regression-class bug. The
remedy is to introduce a primitive that forces the desired ordering,
or to assert a weaker property that holds under any ordering.

### 4. Every test asserts one of three outcomes

A chaos scenario must explicitly assert one of:

- **(a) Invariant preserved despite fault.** Example: 1000 threads
  retrying the same offset → exactly one OK, 999 ALREADY_EXISTS;
  ``next_offset`` is exactly 1 regardless of OS scheduling.
- **(b) Clean documented failure.** Example: corrupt catalog row
  raises ``InternalError`` whose message names the offending row's
  identity. The error class and message-shape regression-test the
  operator-facing contract.
- **(c) Graceful degradation.** Example: a second emulator on a
  locked ``data_dir`` raises a recognisable subset of exceptions
  (``IOException``, ``InternalError``, or chained variant) without
  hanging.

A chaos test must not assert "didn't crash" — that's too weak a
contract for a tier designed to enumerate failure modes. The
distinction matters because a real production incident landed
in one of these three buckets, and the operator's runbook reads
from the same taxonomy.

### 5. 60-second per-scenario timeout

`make test-chaos` runs pytest with ``--timeout=60``. Inside the chaos
conftest we re-assert the cap so direct ``pytest tests/chaos/``
invocations get the same bound. Override via ``BQEMU_CHAOS_TIMEOUT``
when debugging.

A scenario that hits the timeout is a bug — either the scenario
hangs (deadlock, missed event), or it's truly slower than the chaos
tier permits and needs to live in a separate ``slow`` tier.

### 6. Environment-dependent scenarios may `pytest.skip`

Two scenarios in Phase 11's initial chaos build-out are
environment-dependent: the spatial-extension-offline scenario in
``test_storage_failures.py`` and the FD-cap scenario in
``test_resource_exhaustion.py``. Both fall back to
``pytest.skip(...)`` with a documented reason when the host's
configuration can't reproduce the fault (DuckDB has a cached
spatial extension; the soft FD limit can't be lowered without
breaking pytest's own logging FDs).

This is acceptable iff:

1. The skip reason is specific and actionable (not "skipped because
   slow").
2. A unit-tier counterpart exists that asserts the same contract
   deterministically. The unit test is the canonical lock; chaos
   adds a process-level dimension for environments that support
   it.

Without the unit-tier counterpart, the scenario must use a different
injection mechanism that doesn't depend on the host's caches.

### 7. Subprocess scenarios share a helper

`tests/chaos/test_crash_recovery.py` and `tests/chaos/test_storage_failures.py`
both spawn subprocesses to inject process-level faults. They share
the ``_spawn_emulator_child`` / ``_wait_for_ready`` pattern: the
child runs a snippet, prints ``READY`` once setup is complete,
then awaits an `asyncio.Event` until the parent kills it.

This pattern matters because it makes the test deterministic —
the parent's kill is synchronised to the child's state transition
rather than to wall-clock time. A more elaborate IPC (named pipes,
sockets) would buy nothing here; stdout line-buffering is sufficient.

## Consequences

- **Positive.** The Phase 10 audit gaps are now property-tested under
  contention rather than just claimed in design notes. The
  exactly-once guarantee on COMMITTED streams, the MV
  collapse-onto-one-recompute guarantee, the catalog corruption
  recovery flow, and the kill-recovery semantics all have explicit
  regression coverage.

- **Positive.** The five-category split keeps the file surface
  manageable. A new chaos scenario maps to one of the five obvious
  files.

- **Positive.** Determinism rules make CI investigation cheap. A
  failure is reproducible from the seed alone; there's no "couldn't
  reproduce locally" failure mode.

- **Negative.** Chaos runs nightly (not on every commit), so a
  scenario regression can land in main and persist for up to 24
  hours. The mitigation is that every chaos scenario also has a
  unit-tier counterpart or an integration-tier prerequisite; the
  unit gates catch most regressions before chaos sees them.

- **Negative.** Subprocess-based scenarios are slower than unit
  tests — typically 0.5-2 seconds each because Python startup +
  emulator initialisation dominates. The 60-second per-scenario
  budget absorbs this without complaint, but it's a real cost
  compared to a pure-Python unit test.

- **Negative.** Two scenarios skip in some environments (spatial
  extension cache present; FD cap can't be lowered). The
  unit-tier counterparts ([``test_engine_spatial.py``](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/storage/test_engine_spatial.py))
  prevent the contract from drifting, but the chaos test's
  process-level coverage is conditional. We accept this rather than
  build elaborate environment manipulation that would itself become
  a maintenance liability.

## Implementation notes

- The ``chaos`` pytest marker is registered in
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml).
- The ``make test-chaos`` target invokes
  ``pytest tests/chaos -m chaos --timeout=60``.
- ``pytest-timeout`` is declared as an explicit testing dependency
  in `pyproject.toml` (not a transitive of another package) so
  ``make dev-setup`` always installs it.
- The chaos seed is logged in pytest's session header, not via
  ``warnings.warn`` (the project's ``filterwarnings = ["error",...]``
  would convert that into a test failure).
- New chaos scenarios should add their audit-gap reference to the
  module docstring; the Phase 11 review (created when Phase 11
  closes) carries the audit-finding traceability table.

## References

- [Tier 7 in the testing-strategy doc](../architecture/testing-strategy.md)
- Phase 11 roadmap doc — chaos section
- Phase 10 review's production-readiness audit
  — the source of the 8 gaps the chaos tier closes (the Phase 11
  review, when Phase 11 closes, will carry the audit-finding
  traceability table)
- ADR [0013](0013-write-api-strategies.md) — exactly-once guarantee
  the retry-storm scenario property-tests
- ADR [0017](0017-materialized-view-refresh.md) — collapse-onto-one-
  recompute guarantee the MV scenario property-tests
- ADR [0019](0019-specialized-types.md) — spatial extension required-
  startup that the gap-#10 chaos scenario verifies the failure path
  of
