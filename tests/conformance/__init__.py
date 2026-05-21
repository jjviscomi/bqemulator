"""Conformance tier (Tier 5) — emulator vs real BigQuery row-for-row diff.

Every fixture in :mod:`tests.conformance.sql_corpus` is a directory
containing ``query.sql``, an optional ``setup.sql`` seed, and a
recorded ``expected.json`` baseline produced by
``scripts/record_conformance_fixtures.py`` against real BigQuery. The
runner replays each fixture against an in-process emulator and diffs
the result with type-aware tolerance documented in ADR 0022.

The design contract is in
:doc:`/adr/0022-conformance-corpus-design`; the rules are summarised in
:doc:`/architecture/testing-strategy` (Tier 5).

Rules (enforced by the runner + ADR 0022):

* **Baselines come only from the recorder.** Hand-editing ``expected.json``
  is a non-negotiable disqualifier (Phase 11 non-negotiable #8) — values
  must be the literal output of a real BigQuery job. The recorder logs
  the BigQuery job ID into the fixture for audit.
* **Pass-rate gate ≥85% (Option A).** Every fixture expected to diverge
  uses ``@pytest.mark.xfail(strict=True, reason="…")`` with a rationale
  rooted in an ADR (typically ADR 0019 for spheroidal-vs-planar
  GEOGRAPHY divergences). Unexpected pass and unexpected fail both fail
  the suite.
* **No time-dependent fixtures.** Queries that depend on
  ``CURRENT_TIMESTAMP()`` or relative ``FOR SYSTEM_TIME AS OF`` are
  excluded by design — their baselines drift on every recording.
  Dynamic time-travel is exercised in integration tests instead.
* **No IAM-policy fixtures.** Row-access policies and IAM
  configuration require dataset-level GoogleSQL DDL that real BigQuery
  rejects on a fresh service-account-owned dataset without org-level
  policy bindings — orthogonal to the SQL-surface charter of this tier.
  Their behaviour is exercised in unit / integration / E2E tiers.

Conformance runs **weekly** in CI (``conformance.yml``, Mondays at
07:23 UTC) and on demand via ``make test-conformance`` —
``GOOGLE_APPLICATION_CREDENTIALS`` must be set and the corpus
re-recorded via the recorder before any fixture is updated.
"""
