# ADR 0028: Differential-tier design contract

- **Status**: Accepted

## Context

The conformance tier (Tier 5, [ADR 0022](0022-conformance-corpus-design.md))
replays every recorded BigQuery baseline against the in-process
emulator with row-for-row tolerance. That makes the corpus the
project's primary parity-with-BigQuery guarantee — a regression in
the corpus means the emulator has drifted from BigQuery's documented
semantics on a specific input.

But the conformance baseline pins a **specific recorded input**, not
the abstract SQL contract. The corpus cannot surface emulator logic
that **accidentally happens to be correct on the recorded setup data
and wrong on permuted setup data**. Concrete failure modes:

- A ``LIMIT N``-without-``ORDER BY`` shortcut that picks the first
  ``N`` rows in DuckDB's storage order, which happens to match
  BigQuery's storage order on the recorded data.
- An aggregate that visits rows in insertion order and produces
  results that depend on that order even though the SQL semantics
  do not.
- A correlated subquery whose evaluation order accidentally matches
  the recorded ordering of its outer row.

These are the **fixture-specific-shortcut bug class**. Workstream
P8.f closes this class with a new tier that re-runs the corpus with
perturbed setup data and asserts the emulator's output still matches
the recorded baseline **under canonical row sorting**.

[ADR 0021](0021-chaos-tier-design-contract.md) captures the Tier 7
chaos contract; [ADR 0025](0025-perf-tier-design-contract.md)
captures the Tier 6 perf contract; this ADR captures the analogous
contract for the differential tier. The chaos / perf / mutation /
differential tiers all ship as manual-only workflows for v1.0 — the
gating / cadence decision is deferred until after the project's
GitHub repo is set up and there is real PR traffic to measure
runtime, flakiness, and runner-cost trade-offs against.

## Decisions

### 1. The differential tier is a Tier 5 sibling, not a new pyramid number

The
[testing-strategy](../architecture/testing-strategy.md) document
defines a seven-tier pyramid. The differential tier reuses **the
same input corpus** as Tier 5 (conformance); only the *setup data*
is rewritten before each replay. Operationally and conceptually
it's a Tier 5 sibling rather than a new pyramid step.

This mirrors the mutation tier ([ADR 0026](0026-mutation-tier-design-contract.md)),
which is also intentionally not numbered into the pyramid because
its unit of analysis is a delta from a stored baseline, not a fresh
assertion.

### 2. Three perturbation modes; v1.0 ships row-order only

| Mode | Name | What it permutes | Re-recording cost | v1.0 status |
|---|---|---|---|---|
| A | Row-order | Reverse the order of every ``INSERT INTO … VALUES (…), (…), …`` tuple list | None — comparator re-uses recorded `expected.json` | **ships** |
| B | Value-shift | Add a fixed offset (``+ 1000`` to ints, ``+ INTERVAL 7 DAY`` to dates) to columns NOT exercised by ``ORDER BY`` / ``WHERE`` / ``LIMIT`` | Requires operator BigQuery time to re-record a perturbed-sibling corpus | **deferred to v1.0.x** |
| C | Schema-reorder | Permute the ``CREATE TABLE`` column order while preserving row identity | Requires operator BigQuery time AND careful per-fixture analysis to identify "projects by name vs by index" | **deferred to v1.0.x** |

Mode A is row-order **reversal** — the parser walks every ``VALUES``
clause in ``setup.sql`` and emits the tuple list in reverse. This
catches the largest class of shortcut bugs (anything sensitive to
"first row inserted" semantics) and has the lowest friction (no
re-recording required because the row *set* is unchanged; only the
*order* changes).

Modes B and C require BigQuery time the project doesn't have for
v1.0; both are tracked as v1.0.x scope.

### 3. Eligibility checks short-circuit non-perturbable fixtures

The runner ships an:func:`tests.conformance._row_order_perturbation.is_perturbable`
gate that returns ``(False, reason)`` for any fixture whose
semantics make row-order perturbation either meaningless (no setup
data) or actively misleading (row order is contractually pinned by
the query). The structural skips:

- No ``setup.sql`` or no ``INSERT … VALUES`` in setup.
- ``setup_rest.json`` setup (Phase 8 row-access fixtures).
- ``headers.json`` caller-identity pinning.
- ``parameters.json`` or ``job_config.json`` pinning request shape.

The semantic skips:

- Query has top-level ``ORDER BY`` — row order is the contract.
- Query has ``LIMIT`` (anywhere; nested or top-level) — content of
  result rows is non-deterministic without ``ORDER BY``, which
  BigQuery itself documents.
- Query uses ``ARRAY_AGG`` / ``STRING_AGG`` / ``ANY_VALUE`` /
  ``APPROX_QUANTILES`` / ``APPROX_TOP_COUNT`` / ``APPROX_TOP_SUM``
  / ``HLL_COUNT.INIT`` / ``HLL_COUNT.MERGE_PARTIAL`` — output content
  depends on input row order by BigQuery's own contract.
- Query uses ``ROW_NUMBER`` / ``RANK`` / ``DENSE_RANK`` /
  ``PERCENT_RANK`` / ``CUME_DIST`` / ``NTILE`` / ``FIRST_VALUE`` /
  ``LAST_VALUE`` / ``NTH_VALUE`` / ``LAG`` / ``LEAD`` —
  conservatively skipped because verifying every ``OVER`` clause
  carries an explicit ``ORDER BY`` requires an OVER-clause parser
  that's out of scope for v1.0.
- Query uses ``TABLESAMPLE`` — sampling is row-order-dependent by
  spec.

The eligibility list is intentionally conservative — false-positive
"divergences" caused by skipping legitimately-non-deterministic
queries would drown the genuine shortcut-bug signal. **The v1.0
differential pass exercises ~77 of the ~1141 SQL fixtures** (the
remainder are skipped per the rules above). The signal-to-noise
ratio matters more than the absolute coverage number.

### 4. Comparator canonical-sorts both sides

For an ``ORDER BY``-less query that produced rows ``[A, B, C]`` in
the recorded baseline, the emulator under perturbation may produce
``[B, A, C]`` — same content, different order. That's not a
divergence; it's an artefact of the order-undefined contract.

The differential comparator canonical-sorts both ``expected.rows``
and the emulator's actual rows by ``json.dumps(row, sort_keys=True,
default=str)`` before running the per-cell diff. A row-order
difference (allowed) is absorbed by the sort; a row-**content**
difference (disallowed) surfaces as a per-cell mismatch.

The recorded response-metadata block (``job_metadata``, set by
[ADR 0022 §3 P7.a extension](0022-conformance-corpus-design.md))
is **stripped from the expected payload** before comparison. The
differential tier's contract is row content under perturbation,
NOT response-object equivalence — the canonical conformance tier
already pins those fields.

### 5. The skip-list is a last-resort, not a workflow

The runner exposes:data:`tests.conformance._row_order_perturbation.PERTURBATION_SKIP_LIST`
mapping fixture IDs to rationale strings. Adding an entry requires:

1. **A code-path-specific rationale**, citing either an ADR or a
   ``docs/reference/out-of-scope.md`` anchor. ``"row order matters
   here"`` is not a valid rationale.
2. **A demonstration that no inline fix and no fixture-level
   ``ORDER BY`` would close the divergence cleanly**. The skip is
   the LAST option, not the first.

The v1.0 skip-list is **empty**: the keyword-based structural skips
in §3 covered every non-perturbable fixture surfaced during the
P8.f triage pass, so no fixture-id-specific skip was required.

### 6. Manual-only CI cadence; gating decision deferred

The
[`differential.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/differential.yml)
workflow ships with ``workflow_dispatch`` only — no ``schedule:``,
no ``push:``, no ``pull_request:``. This matches the policy
chaos.yml, perf.yml, and mutation.yml adopted in the same session
(P8.e): every ancillary tier ships as a manual-only workflow until
empirical data justifies a stricter cadence.

The gating decision (per-PR vs nightly vs release-gate vs
stays-manual) is **explicitly deferred** until after the project's
GitHub repo is set up and there is real PR traffic to characterise
runtime, flakiness, and runner-cost trade-offs against. Candidate
future triggers for evaluation:

- **Per-PR gate (push + pull_request on main)** — the perturbation
  pass runs in ~5 seconds locally for the 77 currently-perturbable
  fixtures, which is comfortably within the per-PR budget. As the
  perturbable set grows (more fixtures, looser eligibility rules),
  per-PR may become unattractive on cost alone.
- **Nightly schedule** — cheaper per-PR but bugs ship to ``main``
  before detection; mitigated by the fact that every fixture in
  the perturbation set has been parity-pinned via the per-PR
  conformance gate first, so the differential tier catches
  *additional* drift, not first-line regressions.
- **Release-gate-only** — invoked as a precondition of the
  ``release/`` branch's gate chain. Cheapest cadence; weakest
  coverage.

The cadence migration is a **separately-scoped decision** that the
release-readiness session (P5) will make once there is real CI
traffic to evaluate against. Until then, the workflow is invoked
manually via the GitHub Actions "Run workflow" button (or
``gh workflow run differential``).

### 7. Triage protocol on divergence

The first manual-dispatch baseline (P8.f) surfaced no
divergences after the structural eligibility rules in §3 closed all
non-perturbable surfaces. Future divergences are triaged per:

| Outcome | Action |
|---|---|
| **Emulator bug** (storage-order shortcut in the translator / executor / aggregator) | Fix inline; remove from skip-list if present. The fix's regression test goes in the unit tier — the differential tier is the *detection* mechanism, not the regression test. |
| **Fixture has implicit row-order assumption** | Add explicit ``ORDER BY`` to ``query.sql``; re-record against BigQuery (deferred to v1.0.x if operator credentials are unavailable in-session). The re-recording validates that real BigQuery returns the same rows the original fixture asserted under the new ``ORDER BY``. |
| **Row order is semantically meaningful and cannot be ``ORDER BY``-pinned** | Add to:data:`PERTURBATION_SKIP_LIST` with an ADR or ``out-of-scope.md`` anchor. The skip is the LAST option, not the first — see §5. |
| **Perturbation parser is wrong** | Adjust the parser in ``tests/conformance/_row_order_perturbation.py``; do not paper over with a skip-list entry. |

## Consequences

- **Positive.** The fixture-specific-shortcut bug class is now
  property-tested at scale. The v1.0 baseline establishes "the
  emulator's row-content semantics are storage-order-independent
  on the 77 perturbable surfaces", and any future regression
  surfaces in the manual-dispatch run.

- **Positive.** Re-using the existing conformance baselines (no
  re-recording required for mode A) means the tier shipped without
  any operator BigQuery time. This is the same forcing-function
  property that made the parity-locked corpus cheap to maintain:
  the baseline is canonical, the perturbation is local.

- **Positive.** The structural eligibility rules in §3 produced an
  **empty skip-list** on the first run. Every non-perturbable
  fixture was caught by the keyword-based filter — no fixture-id-
  specific carve-outs were needed. This is the desired equilibrium:
  the discipline (skip-list with rationales) exists for future
  divergences, but the v1.0 baseline doesn't pay an upfront cost
  for it.

- **Positive.** The manual-only cadence mirrors chaos / perf /
  mutation, so the operator workflow is consistent across all four
  ancillary tiers. A new contributor who knows how to invoke perf
  knows how to invoke differential without re-reading the docs.

- **Negative.** v1.0 ships **row-order perturbation only**. Modes B
  (value-shift) and C (schema-reorder) are deferred to v1.0.x. The
  v1.0 baseline therefore does NOT close the "emulator silently
  depends on specific column ordinals" or "emulator silently depends
  on specific value ranges" sub-classes of the shortcut-bug family.
  The mitigation is the variation-taxonomy coverage matrix (P8.a)
 + the edge-case sweep (P8.b) + the TPC-H/DS expansion (P8.c, P8.d)
 + the timezone sweep (P8.e), which together exercise enough value
   / schema variation that a coarse value-shift or schema-reorder
   divergence would surface as a recorded-baseline divergence first.

- **Negative.** The conservative keyword filter in §3 excludes
  ~93% of SQL fixtures from the perturbable set (1141 → 77). The
  excluded fixtures are NOT perturbation-untested in the sense
  that "no test exercises them under storage-order perturbation",
  but they are excluded from THIS tier because their queries use
  contracts that BigQuery itself documents as order-sensitive
  (``LIMIT``, ``ORDER BY``-less aggregates, etc.). The right
  closure for any specific excluded fixture is to add an explicit
  ``ORDER BY`` (where the test surface allows) and re-record;
  v1.0.x will do this opportunistically rather than as a batched
  workstream.

- **Negative.** The cadence decision is deferred. A bug class that
  the differential tier catches will sit in ``main`` until an
  operator manually dispatches the workflow — there is no
  automatic guard. The mitigation is that the conformance tier's
  per-PR gate catches all "regression vs recorded baseline" bugs
  first; the differential tier only catches "shortcut bug that
  happens to match the recorded baseline", which is a narrower
  class. The post-repo-setup cadence evaluation will revisit this.

## Implementation notes

- The ``differential`` pytest marker is registered in
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml).
- The ``make test-differential`` target invokes
  ``pytest tests/conformance/test_corpus_row_order_perturbed.py -m differential
  --junit-xml=differential-results.xml``. The JUnit XML is the artefact
  the manual workflow uploads.
- The conformance conftest at
  [`tests/conformance/conftest.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/conftest.py)
  auto-applies the per-tier marker — files under ``tests/conformance/``
  get the ``conformance`` marker EXCEPT ``test_corpus_row_order_perturbed.py``
  which gets the ``differential`` marker instead. The per-PR
  conformance gate filters on ``-m conformance`` and so excludes
  the differential tests automatically.
- The differential test module **deliberately duplicates** the
  ``_result_to_rows`` / ``_result_to_schema`` helpers from
  ``test_corpus.py`` rather than refactoring them into a shared
  helper. Refactoring would touch the canonical runner; that's a
  bigger blast-radius than the P8.f session's medium-risk budget
  allows. The v1.0.x follow-up that adds modes B / C will likely
  introduce a shared ``_runner.py`` helper module.

## References

- [Tier 5 in the testing-strategy doc](../architecture/testing-strategy.md)
- v1-confidence-plan workstream P8.f
  — this ADR closes the workstream
- ADR [0021](0021-chaos-tier-design-contract.md) — Tier 7 (chaos)
  design contract, structurally analogous to this ADR
- ADR [0022](0022-conformance-corpus-design.md) — Tier 5 (conformance)
  design contract, the parent tier whose corpus the differential
  tier re-uses
- ADR [0025](0025-perf-tier-design-contract.md) — Tier 6 (perf)
  design contract, the chronologically-prior sibling ADR
- ADR [0026](0026-mutation-tier-design-contract.md) — mutation-tier
  design contract; the "comparison gate alongside the pyramid"
  precedent for an ancillary tier
- [`tests/conformance/_row_order_perturbation.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_row_order_perturbation.py)
  — the perturbation parser + eligibility gate
- [`tests/conformance/test_corpus_row_order_perturbed.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/test_corpus_row_order_perturbed.py)
  — the runner
- [`.github/workflows/differential.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/differential.yml)
  — the manual-dispatch workflow
