"""Chaos tier (Tier 7) — deliberately disruptive tests.

Every test in this package injects a real failure mode (resource
exhaustion, crash, network drop, race) and asserts the emulator
either preserves an invariant, fails in a clean documented way, or
gracefully degrades. The design contract is in
:doc:`/adr/0021-chaos-tier-design-contract`; the rules are summarised
in :doc:`/architecture/testing-strategy` (Tier 7).

Rules (enforced by the runner + the tests themselves):

* **Deterministic given a seed.** No timing-dependent races; concurrent
  ordering is forced via ``threading.Barrier``/``Event`` so every run
  reproduces the same interleaving.
* **<60s per scenario.** ``make test-chaos`` invokes pytest with
  ``--timeout=60``; a hung scenario is a bug.
* **Asserts one of three outcomes.** (a) invariant preserved despite
  fault, (b) clean documented failure (a specific error class with a
  specific message shape), or (c) graceful degradation (e.g. partial
  result + warning).

Chaos tests run **nightly** in CI; locally invoke via ``make
test-chaos``. They are not part of the standard ``pytest`` / commit
gate because some scenarios (subprocess kills, file-locked retries)
are slower than the unit/integration tiers permit on every push.
"""
