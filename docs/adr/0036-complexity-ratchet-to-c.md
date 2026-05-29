# ADR 0036: Cyclomatic-complexity ratchet to rank C + promote-to-required gate

- **Status**: Accepted

## Context

[ADR 0035](0035-code-quality-gates.md) landed the non-blocking
quality gates — radon / xenon for complexity, jscpd for duplication,
vulture for dead code — with thresholds calibrated against the
v1.0.2 codebase. The xenon threshold was set to
`--max-absolute E --max-modules C --max-average A`: deliberately
permissive so the baseline passed without forcing any refactors.
ADR 0035 also documented the promotion-to-required pattern as a
follow-up PR once the baselines settled.

The project owner asked to tighten the absolute ceiling from rank E
(complexity ≤ 40) to **rank C (complexity ≤ 20)** as the standing
standard. The reasoning: rank D-E functions in the codebase are
mostly "type-dispatch grew accumulated cruft" — the kind of
complexity that a behavior-preserving dispatch-table refactor
collapses, not domain-shaped complexity that the dispatch pattern
hides. ADR 0035 had already noted that ruff's intentionally-ignored
`C901` / `PLR0911` / `PLR0912` (the inline complexity rules) were
suppressed because per-function `noqa` was noisy, NOT because the
underlying complexity was justified — so a refactor pass that
brings everything under rank C and then enforces it absolutely is
the lower-friction path forward.

## Baseline audit (against `main` post-PR-#44)

`radon cc src/bqemulator -nD -s` returned 10 functions across 8
modules. 3 at rank E, 7 at rank D:

| # | File | Line | Function | Rank | CC |
|---|---|---|---|---|---|
| 1 | `storage/arrow_bridge.py` | 159 | `_format_bq_value` | E | 39 |
| 2 | `storage/arrow_bridge.py` | 362 | `_coerce_to_arrow_value` | E | 33 |
| 3 | `jobs/executor.py` | 1361 | `classify_statement_type` | E | 31 |
| 4 | `catalog/memory_repository.py` | 89 | `MemoryCatalogRepository.delete_dataset` | D | 26 |
| 5 | `streaming/avro_serializer.py` | 193 | `_arrow_dtype_to_avro` | D | 26 |
| 6 | `types/interval.py` | 153 | `_consume_blocks` | D | 24 |
| 7 | `scripting/parser.py` | 153 | `Parser._parse_statement` | D | 23 |
| 8 | `api/routes/jobs.py` | 901 | `_apply_write_append` | D | 23 |
| 9 | `api/routes/tables.py` | 149 | `_rest_to_table_meta` | D | 22 |
| 10 | `scripting/parser.py` | 647 | `Parser._read_type_name` | D | 21 |

`xenon --max-absolute C --max-modules C --max-average A src/bqemulator`
exit 1 against this baseline.

## Decision

1. **Refactor every rank-D / rank-E function above to rank ≤ C** —
   behavior-preserving, no semantic changes, no new tests beyond
   what the existing suite already exercises. Each refactor follows
   one of three patterns:

   | Bucket | Pattern | Used by |
   |---|---|---|
   | **A** Clean refactor | Extract sub-blocks as named helpers; main function reads as a sequence of named steps. | `delete_dataset`, `_consume_blocks`, `_apply_write_append`, `_rest_to_table_meta`, `Parser._read_type_name` |
   | **B** Dispatch table | Long `if/elif` chain over typed predicates → `tuple[(predicate, handler)]` + a loop in the caller. | `_format_bq_value`, `_coerce_to_arrow_value`, `classify_statement_type`, `_arrow_dtype_to_avro`, `Parser._parse_statement` |
   | **C** Irreducibly branchy | Documented xenon `--exclude` carve-out + ADR-recorded "this is domain-shaped" reasoning. | **none** — every function in the audit list fell into bucket A or B |

   No bucket-C exclusions are needed. The original ADR-0035 framing
   anticipated up to ~20% of the list might be irreducible; the
   actual rate is 0%.

2. **Tighten the Makefile gate** to
   `xenon --max-absolute C --max-modules C --max-average A`. Today's
   refactored codebase passes; any new function above rank C either
   (a) gets a dispatch-table or helper-extraction refactor, or (b)
   ships a separate PR adding a `--exclude` path pattern with a new
   ADR section justifying the irreducibility verdict.

3. **Promote the gate to required**:

   - Add `make quality-complexity` to the `make verify` chain.
   - Drop `continue-on-error: true` from the cyclomatic-complexity
     step in `.github/workflows/code-quality.yml`. The duplication
     and dead-code steps keep their non-blocking status — those
     gates get their own promote-to-required ratchet PRs when
     their baselines settle.
   - Add the `Quality gates` check job to the branch-protection
     ruleset's `required_status_checks.contexts` list (ruleset id
     `16726422`).

4. **Stable job name** — rename the workflow's job from
   `Quality gates (non-blocking)` to plain `Quality gates`. Branch
   protection matches by job name; keeping the name short and
   blocking-status-agnostic means duplication / dead-code can
   promote later without invalidating the required-check entry.

## Refactor results

Per-function before / after, captured live during the work:

| Function | Before | After |
|---|---|---|
| `_format_bq_value` | E (39) | **C (14)** |
| `_coerce_to_arrow_value` | E (33) | **B (7)** |
| `classify_statement_type` | E (31) | **B (9)** |
| `delete_dataset` | D (26) | **B (6)** |
| `_arrow_dtype_to_avro` | D (26) | **A (4)** |
| `_consume_blocks` | D (24) | **A (2)** |
| `_parse_statement` | D (23) | **A (5)** |
| `_apply_write_append` | D (23) | **B (10)** |
| `_rest_to_table_meta` | D (22) | **B (8)** |
| `_read_type_name` | D (21) | **C (13)** |

Project-wide average complexity stays at rank A (around 3.4).
Module averages stay ≤ rank C. `radon cc -nD -s` is now empty.

The new helper functions (one per extracted sub-block; one per
dispatch-table handler) all land at rank A or low B — the helpers'
cumulative complexity is roughly equal to the original function's
because cyclomatic complexity is additive across called functions
when counted with radon's per-function model, but the dispatch
pattern makes the BRANCH STRUCTURE local rather than nested, which
is what the gate is calibrated for.

## Rationale

### Why rank C, not D

Rank D (complexity 21-30) covers functions that need refactoring
*today* on most code-quality scoring rubrics (radon's documented
recommendation is "consider refactoring at rank C"). Rank C marks
the threshold where the function is "still grokkable" — testable
without coverage holes, refactorable without changing semantics.
Anchoring the absolute ceiling at C aligns the gate with the
project-wide judgment that type-dispatch is fine *as a pattern*
but not as inline branching: the dispatch should be in a table,
not a procedural chain.

### Why no bucket-C exclusions

The audit list was uniformly amenable to dispatch-table or
helper-extraction refactors. The historical framing
(ADR 0035 "if you find yourself reaching for bucket C more than
~20% of the list, you're probably wrong about the bucketing")
turned out to be conservative. The bucket-C escape hatch stays
*available* — if a future PR adds genuine domain-shaped complexity
(e.g. a SQL grammar parser, a constraint-solver dispatch), the
contributor adds a `--exclude` plus an ADR section. The bar is
"another maintainer reading the function would agree the
complexity is domain-shaped, not accumulated."

### Why promote in the same PR as the refactors

Two reasons:

1. **Test-then-refactor hygiene works at the gate level too.** With
   the gate at rank E for non-blocking, a regressing function
   wouldn't even be flagged in CI — it'd silently pass. Tightening
   the gate without the refactors first would have failed CI on
   day one. Tightening after the refactors land but without
   promoting it to required means a new D-rank function could
   merge against the still-passing non-blocking gate. Atomic
   atomicity wins.

2. **Branch protection lists by job name.** The "Quality gates"
   job has run on every PR since PR #44; renaming it to add the
   "(required)" suffix or splitting it into two jobs would
   invalidate the existing job-name match the ruleset will
   eventually depend on. Keep the stable name, flip the
   `continue-on-error` flag, and add to required-checks in one
   atomic step.

### Why duplication / dead-code stay non-blocking

Both have a different cost/benefit shape:

- **jscpd duplication** baseline is 0.36% (seven clones, 112
  lines, all 11-22 lines and structurally template-shaped — see
  ADR 0035). Refactoring those would introduce premature
  abstraction; the gate is calibrated to catch *new* drift, not to
  eliminate the baseline. The threshold (1.0%) catches regression
  but doesn't force the cleanup.
- **vulture dead-code** finds one false positive (the
  reserved-for-future `use_cache` kwarg, already whitelisted).
  There's nothing to refactor against; promoting to required is
  effectively a no-op on the current state.

Each gets its own ratchet PR if a more aggressive posture becomes
useful. For now: the noise is too low to justify the merge-blocking
cost.

## Consequences

### Positive

- The codebase's worst function complexity drops from 39 to 14.
  Every new function or modified function has a per-PR signal at
  rank C — drift past that fails CI loudly.
- The bucket-A / bucket-B refactor patterns are now documented in
  the helpers' docstrings (see e.g.
  `arrow_bridge._fmt_bq_*` / `arrow_bridge._coerce_arrow_*` /
  `executor._classify_*` / `tables._rest_to_*`) so future
  contributors have a template.
- The "type-dispatch is naturally branchy" ruff-ignore rationale in
  pyproject.toml stays accurate — but it now reads as "ruff's
  inline `C901` is noisy *AND* the project enforces a stricter
  rank ceiling externally via xenon." Two checks; one is local
  per-call, the other is project-wide structural.

### Negative

- The branch-protection ruleset now has another required check; a
  PR can no longer merge with a complexity regression. New
  contributors who introduce a rank-D function need to refactor
  before merge. The Makefile target's docstring + ADR pointer
  documents the expected pattern (dispatch table or helper
  extraction) so the friction is bounded.
- The `make verify` chain now runs xenon as part of the
  pre-commit gate. Local cost: ~1 second. CI cost: same step but
  no longer wrapped in `continue-on-error` — so a flake there
  fails the workflow. xenon has no known flakiness modes (it's
  deterministic against the radon AST output), so this is a
  theoretical concern.

### Neutral

- Helper-function count grew by ~30 across 6 source files. Code
  size diff is roughly +475 / -316 net + several added module-
  level dispatch tables. Test count is unchanged (3127 passed +
  1 expected skip both before and after).
- The duplication and dead-code gates' continue-on-error wrappers
  stay. Until each promotes, the workflow summary's outcomes
  table makes the blocking / non-blocking split explicit.

## Alternatives considered

1. **Refactor in waves, gate-promote in a separate PR.** Cleaner
   for review but invites drift between landing and enforcement —
   a regressing function merged in between would silently pass.
2. **Per-function `# xenon: ignore` annotations.** xenon doesn't
   support per-function ignores natively. Adding the metadata
   would require either a fork or a pre-/post-processor; both are
   higher cost than the path-level exclusion pattern this ADR
   adopts.
3. **Tighten to rank B (complexity ≤ 10).** Too strict — many
   structurally reasonable Python functions land at rank B-C
   (e.g. argument validators, REST request normalisers). The
   rank C threshold matches the "still grokkable" bar without
   forcing rewrites of perfectly clear code.
4. **Use pylint's `max-complexity` instead.** pylint is not in
   the project's lint chain (ruff covers everything pylint does
   on this codebase plus more). Adding pylint just for one
   metric duplicates infrastructure.
5. **Switch to a different complexity metric (Halstead, cognitive).**
   radon supports Halstead and maintainability index but the team
   familiarity is with cyclomatic. The rank scale is documented
   and standard. Reconsider if cyclomatic ever proves to miss
   real complexity (it sometimes does for callback-heavy code,
   but this codebase is procedural).

## References

- [ADR 0035](0035-code-quality-gates.md) — the parent decision
  introducing the gates; this ADR tightens one of them.
- [radon documentation](https://radon.readthedocs.io/en/latest/) —
  rank-tier definitions (A: 1-5, B: 6-10, C: 11-20, D: 21-30,
  E: 31-40, F: ≥41).
- [xenon documentation](https://xenon.readthedocs.io/en/latest/) —
  CLI threshold semantics (`--max-absolute`, `--max-modules`,
  `--max-average`).
- AGENTS.md "Pre-commit gate (mandatory)" — the contract this ADR
  extends by adding `make quality-complexity` to `make verify`.

## Update (2026-05-28): follow-up ratchet to rank B in ADR 0041

The C-ratchet ran for ~6 months and ADR 0036's "Alternatives
considered" §3 rejection of an immediate rank-B ceiling has been
revisited.

The original verdict — "many structurally reasonable Python functions
land at rank B-C" — held only until a sustained refactor pass tested
it empirically. The PR-1 through PR-11 sweep
([#90](https://github.com/jjviscomi/bqemulator/pull/90) through
[#102](https://github.com/jjviscomi/bqemulator/pull/102)) closed
every remaining rank-C function in `src/` (~60 functions across 26
files) using the same bucket-A (helper extraction) and bucket-B
(dispatch table) patterns this ADR established for the D/E sweep.
The bucket-C "irreducible domain complexity" escape hatch — predicted
to absorb up to ~20% of refactor targets — stayed at 0 hits, just
like it did for the D/E audit.

[ADR 0041](0041-complexity-ratchet-to-b.md) ratchets the
`--max-absolute` ceiling from rank C to rank B
(`xenon --max-absolute B --max-modules C --max-average A`) on top
of this ADR's foundations. The two ADRs co-exist:

* **This ADR (0036) remains in effect** for everything except the
  per-function absolute ceiling — the gate's promotion-to-required
  status, the bucket A/B/C taxonomy, the duplication / dead-code
  gates' non-blocking posture, the rationale for keeping the
  project-wide average at rank A and module average at rank C.
* **ADR 0041 layers** a tighter `--max-absolute` on top after the
  PR-1…PR-11 sweep demonstrated empirically that the rank-C bucket
  was uniformly refactorable.

ADR 0036 is not superseded — it's the ground floor that ADR 0041
builds on.
