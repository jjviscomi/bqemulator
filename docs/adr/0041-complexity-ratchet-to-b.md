# ADR 0041: Cyclomatic-complexity ratchet to rank B + campaign retrospective

- **Status**: Accepted

## Context

[ADR 0035](0035-code-quality-gates.md) introduced the non-blocking
quality gates with the threshold deliberately permissive
(`--max-absolute E`). [ADR 0036](0036-complexity-ratchet-to-c.md)
ratcheted the absolute ceiling to **rank C** (complexity ≤ 20) and
promoted the gate to required, refactoring 10 D/E-rank functions in
the same atomic PR.

ADR 0036 §"Alternatives considered" §3 explicitly evaluated tightening
to **rank B** (complexity ≤ 10) and rejected it as "too strict — many
structurally reasonable Python functions land at rank B-C." That
verdict was correct *at that time*: an immediate B-cap would have
trapped well-designed dispatch / state-machine functions and forced
either widespread `--exclude` carve-outs or premature abstraction.

The C threshold ran for ~6 months (PR #44 landing → today). During
that window the rank-C bucket revealed itself as a different shape
than ADR 0036 anticipated:

* Most rank-C functions weren't structurally reasonable — they were
  the *next layer* of "type-dispatch grew accumulated cruft" that
  ADR 0036's refactors hadn't reached.
* Behaviour-preserving dispatch-table + helper-extraction patterns
  (Buckets A/B from ADR 0036) collapsed every rank-C function the
  campaign attempted — same as the D/E sweep. The bucket-C "irreducible
  domain complexity" escape hatch stayed at 0 hits, exactly like
  ADR 0036's audit list.
* The thresholds at which the dispatch pattern stops being a clear
  improvement turned out to be lower than rank B's 10-complexity
  ceiling: the helpers produced by C→B refactors land at rank A
  (CC 1–5) almost universally; very few of the new helper functions
  sit in the rank-B band themselves.

In other words: the codebase converged on a structural style where
rank-A is the common case and rank-B is the "edge of natural
complexity" boundary. Rank-C functions look like outliers, not
typical code. Tightening the ceiling to B aligns the gate with the
observed structural floor of the codebase, not against a hypothetical
"refactor everything" mandate.

## Baseline audit (campaign sweep PR-1 through PR-11)

`radon cc src/bqemulator -nC -s` against `main` at the start of the
campaign returned **~60 rank-C functions across 26 files**. The
campaign closed them in 11 sequential PRs, each scoped to a single
package or subsystem so review surface stayed bounded and the
behavioural impact of each refactor was independently reviewable.

| PR | Commit | Scope | Files | C-blocks closed |
|---|---|---|---|---|
| 1 | `fb46d9e` ([#90](https://github.com/jjviscomi/bqemulator/pull/90)) | storage type / value mappers | `storage/arrow_bridge.py`, `storage/type_map.py` | 4 |
| 2 | `045493a` ([#91](https://github.com/jjviscomi/bqemulator/pull/91)) | REST DTO mappers | `api/routes/{datasets,routines,tables}.py` | 3 |
| 3 | `f1a9109` ([#92](https://github.com/jjviscomi/bqemulator/pull/92)) | misc leaf blocks | 3 files | 3 |
| 4 | `d7baaba` ([#93](https://github.com/jjviscomi/bqemulator/pull/93)) | REST request handlers | `api/routes/jobs.py` etc. | 5 |
| 5 | `cdf5de0` ([#94](https://github.com/jjviscomi/bqemulator/pull/94)) | catalog + versioning | `catalog/*`, `versioning/*` | 5 |
| 6 | `21000c3` ([#95](https://github.com/jjviscomi/bqemulator/pull/95)) | builtin UDFs | `sql/builtin_udfs.py` | 6 |
| 7 | `a17ce60` ([#96](https://github.com/jjviscomi/bqemulator/pull/96)) | SQL rewriters | `sql/rewriter/*.py` (8 files) | 9 |
| 8 | `1fc1e14` ([#98](https://github.com/jjviscomi/bqemulator/pull/98)) | SQL core | `sql/{table_rewriter,parameters,translator}.py` | 4 |
| 9 | `f72314f` ([#99](https://github.com/jjviscomi/bqemulator/pull/99)) | gRPC + streaming | `grpc_api/*`, `streaming/read_session.py` | 4 |
| 10 | `bf62f8e` ([#100](https://github.com/jjviscomi/bqemulator/pull/100)) | scripting | `scripting/{lexer,parser,interpreter}.py` | 8 |
| 11 | `2a87499` ([#102](https://github.com/jjviscomi/bqemulator/pull/102)) | jobs | `jobs/{avro_reader,upload_session_manager,error_mapper,executor}.py` | 9 |

`xenon --max-absolute B --max-modules C --max-average A src/bqemulator`
passes against `main` after PR-11 landed. `radon cc src/ -s -n C` is
empty. Project-wide CC average sits at **A (3.06)** — improved from
the ~3.4 ADR 0036 reported, because the new helpers introduced by
the dispatch-table pattern overwhelmingly land at rank A (CC 1–5).

## Decision

1. **Tighten the absolute ceiling to rank B** in the Makefile gate:

   ```
   xenon --max-absolute B --max-modules C --max-average A src/bqemulator
   ```

   * `--max-absolute B` — no function above rank B (CC > 10). A new
     C-rank function fails CI loudly, exactly the way a new D-rank
     function did under the old gate.
   * `--max-modules C` — **stays at C**. Module averages are
     aggregate; some modules carry justified rank-B clusters
     (e.g. SQL builtin-UDF rule classes) where the per-function
     pattern is correct but the average drifts slightly above A.
     Tightening this further would either force premature
     abstraction or push the rank-B helpers below their natural
     readable size.
   * `--max-average A` — **stays at A**. Project-wide CC average is
     3.06 (well below the 5.0 rank-A ceiling).

2. **No additional refactors required.** The PR-1 through PR-11
   campaign closed every existing C-rank function. The B-ceiling
   flip is a single-line config change against an already-compliant
   codebase. This is the same atomic "refactor first, then ratchet"
   discipline ADR 0036 established — the only difference is that the
   refactor work was spread across 11 PRs rather than one.

3. **No bucket-C exclusions added.** The 60+ rank-C functions
   closed during PR-1 through PR-11 were all bucket-A (clean
   refactor) or bucket-B (dispatch table). The bucket-C escape
   hatch from ADR 0036 stays available for any future genuine
   domain-shaped complexity, but the empirical rate after 70+
   refactors is 0%.

4. **ADR 0036 remains in effect.** The C-ratchet ADR is not
   superseded — its decisions about the gate's promotion-to-required
   status, the bucket A/B/C taxonomy, and the duplication / dead-code
   gates' non-blocking posture all stand. This ADR layers a tighter
   absolute ceiling on top.

## Refactor results (campaign-wide)

Per-PR before / after counts captured live during the work:

| PR | Functions touched | Worst rank before | Worst rank after |
|---|---|---|---|
| 1 | 4 | C (17) | A (4) |
| 2 | 3 | C (15) | A (4) |
| 3 | 3 | C (16) | A (5) |
| 4 | 5 | C (18) | A (5) |
| 5 | 5 | C (15) | A (4) |
| 6 | 6 | C (15) | A (5) |
| 7 | 9 | C (19) | A (5) |
| 8 | 4 | C (14) | A (4) |
| 9 | 4 | C (17) | A (5) |
| 10 | 8 | C (16) | A (6) |
| 11 | 9 | C (20) | A (4) |

Notable peaks (highest pre-refactor complexity in the campaign) all
hit at rank C(20)—the upper edge of the rank-C band:

* `jobs/error_mapper.translate_runtime_error` C(20) → A(4) via
  `_DUCKDB_TRANSLATORS` dispatch tuple (PR-11)
* `jobs/executor.execute_load_job` C(20) → ≤B via `_LoadJobConfig`
  dataclass + per-format dispatch (PR-11)
* `jobs/executor._arrow_type_to_bq_type` C(19) → A(3) via
  `_ARROW_TO_BQ_RULES` dispatch tuple (PR-11)
* `sql/rewriter/...` C(19) functions → A/low-B via per-rule helpers
  (PR-7)

The new helper functions (one per extracted sub-block; one per
dispatch-table handler) all land at rank A or low B — the same
shape ADR 0036 observed at the D→C ratchet, repeated at scale.

## Rationale

### Why rank B, not stay at C

Three converging signals:

1. **The C-ratchet revealed bucket-A/B exhaust at zero.** ADR 0036
   anticipated up to ~20% of the rank-D/E audit list might be
   irreducibly complex; the observed rate was 0%. PR-1 through PR-11
   tested the same hypothesis at the C-band — another ~60 functions
   refactored with the same bucket-A/B patterns, same 0% irreducible
   rate. The codebase doesn't carry genuine domain-shaped complexity
   in the C-band that resists dispatch-table refactoring.

2. **The post-refactor codebase converged on rank A.** New helpers
   land at A (CC 1-5) almost universally. The natural structural
   floor of the post-campaign codebase is "rank A is common, rank B
   is rare." Setting the gate to rank-B caps the worst case at that
   observed structural ceiling.

3. **Drift detection wins.** A new function that lands at rank C
   under the old gate would silently pass; the same function fails
   loudly under the new gate. Given the campaign closed every existing
   rank-C function, "no rank-C functions" is a stable steady-state
   the gate can defend.

ADR 0036's original rejection of rank B was correct for its moment:
without the campaign, the rank-C bucket carried justifiable
"structurally reasonable Python" complexity. After the campaign, the
remaining rank-C functions were the absence — there are none.

### Why a multi-PR sweep, not one big bang

The C-band had ~60 functions to close, vs. the 10 D/E-rank functions
ADR 0036 closed in a single PR. Two reasons not to repeat the
big-bang shape:

1. **Review surface.** ADR 0036's PR landed +475/-316 lines across
   6 source files. A C-band big-bang would have been +3000-5000
   across 26 files, which exceeds the size at which a reviewer can
   reliably verify behaviour preservation per-hunk.

2. **Subsystem coherence.** The C-band functions clustered by
   subsystem (storage types, REST mappers, SQL rewriters,
   scripting, jobs). Per-subsystem PRs let each one ship with a
   focused validation gate (e.g. PR-9's full e2e-against-Storage-API
   matrix; PR-10's full scripting conformance run) without the
   distraction of unrelated refactor noise.

The trade-off: longer end-to-end timeline (~3 months wall-clock for
PR-1 through PR-11), more merge-coordination overhead, and a window
where main has the gate at C but is *converging* on B-readiness.
Acceptable trade-off given the review-surface alternative.

### Why we keep `--max-modules` at C

Module averages aren't the same metric as per-function ceilings. A
module with 100 rank-A functions and 1 rank-B function averages
~A, but a module with 20 rank-A functions and 5 rank-B functions
might average mid-B. Tightening `--max-modules` to B would surface
the latter without distinguishing "5 reasonable rank-B helpers" from
"5 ratty rank-B functions due for a rewrite."

The aggregate is a different concern from the per-function ceiling.
Today's project-wide module averages are all ≤ rank C; some modules
sit at low-B because their natural shape carries clusters of
"clean small dispatch functions" each at rank A-B. Keeping
`--max-modules C` lets that cluster exist; the per-function gate
catches any one of them growing too large.

### Why no bucket-C exclusions emerged

Two structural reasons:

1. **The dispatch pattern is general.** Both bucket-A (helper
   extraction) and bucket-B (dispatch table) are language-neutral
   refactoring patterns that work for almost any "long if/elif
   chain" or "long procedural function." The codebase's complexity
   is overwhelmingly of those two shapes — type dispatch, REST
   field mapping, SQL rewriter rule application, AST traversal —
   which all dispatch cleanly.

2. **Genuine domain complexity hides in *systems*, not functions.**
   The places where the emulator carries genuinely irreducible
   complexity (the SQL translator pipeline, the scripting
   interpreter's control-flow handling, the conformance runner's
   comparator) are *multi-function systems* with rank-A or low-B
   individual functions. Cyclomatic complexity per-function is the
   wrong lens for those — they're complex by design at the
   *interaction* level, not the per-function-control-flow level.

The bucket-C exclusion escape hatch stays available — a future
contributor might find a genuine SQL grammar parser node where a
20-way switch is the cleanest model. The bar is unchanged from
ADR 0036: "another maintainer reading the function would agree the
complexity is domain-shaped, not accumulated."

## Consequences

### Positive

* The codebase's worst function complexity drops from rank C (CC 20)
  to rank B (CC ≤ 10). The dispatch-table + helper-extraction
  patterns are now consistently applied across the codebase's
  branchy hot spots — type mappers, AST traversal, error mapping,
  per-format job loaders, BQ wire-format translators.
* New contributors writing a rank-C function get the same loud
  CI failure that ADR 0036 introduced for rank-D — the gate's
  per-PR signal scales with the actual codebase quality bar,
  not a historical baseline.
* The campaign documented the 11-PR ratchet pattern as a *reusable*
  approach. Future ratchets (e.g. tightening `--max-modules`,
  promoting jscpd to required, tightening vulture's confidence
  threshold) can follow the same per-subsystem-PR shape with
  predictable review-surface bounds.
* Project-wide CC average improved from ~3.4 (post-ADR 0036) to
  3.06. The dispatch-pattern helpers don't just preserve average
  complexity — they slightly improve it, because the per-function
  branch count is now distributed across more (simpler) functions.

### Negative

* The gate is now strict enough that some natural-shape "small
  state-machine functions" might trip it. If a future contributor
  hits this with a genuine domain-shaped function, they have two
  paths: refactor (the campaign's experience says this works ~100%
  of the time for the patterns the codebase already carries), or
  open a bucket-C exclusion PR with an additive ADR section. The
  friction is bounded; the alternative — letting rank-C functions
  accumulate again — would unwind the campaign.
* The `make verify` chain's complexity step continues to enforce
  the new threshold locally. No new local cost (xenon's runtime is
  dominated by I/O, not the threshold value), but a new contributor
  unfamiliar with the campaign might land their first rank-C
  function and need to read this ADR + ADR 0036 to understand the
  pattern. The Makefile's docstring + the dispatch-table examples
  in the codebase (`_DUCKDB_TRANSLATORS`, `_ARROW_TO_BQ_RULES`,
  `_LOAD_FORMAT_HANDLERS`, `_STATEMENT_DISPATCH`, etc.) document
  the expected shape.

### Neutral

* The refactor + ratchet split (PR-1 through PR-11 closed the
  C-band; PR-12 flips the gate) means main went through a window
  where it was "rank-C-clean but gate-still-at-C." That window
  closes with this PR. No production effect — the emulator's
  runtime behaviour is identical pre- and post-campaign.
* Helper-function count grew by ~150-200 across the 11 PRs. Module
  sizes drifted up by a similar margin; some files (notably
  `jobs/executor.py` and `scripting/interpreter.py`) gained
  ~30-50% LOC. Maintainability index per-module stayed
  rank-A or rank-B throughout — the additional helpers compensate
  for their inline-branch counterparts.
* CHANGELOG entry deferred to release time per the
  project's [release-time-authored CHANGELOG policy](https://github.com/jjviscomi/bqemulator/blob/main/CHANGELOG.md).
  This ADR is the authoritative record of the campaign until the
  next release synthesises a CHANGELOG bullet from `git log`.

## Alternatives considered

1. **Stay at rank C indefinitely.** Would have left the gate
   defending a baseline that the codebase had outgrown.
   Post-campaign, the natural structural floor is rank A — a gate
   at rank C carries no defensive value once the rank-C band is
   empty.

2. **Skip ADR 0041, fold the gate flip into PR-11.** Considered;
   rejected. The doc retrospective + Makefile flip is a separate
   logical change from "refactor jobs/* functions." Keeping them
   distinct preserves the bisect signal — if a future regression
   surfaces against the B-gate, `git log --grep "ratchet to rank B"`
   points directly to this PR.

3. **Ratchet to rank A (CC ≤ 5).** Too strict. The rank-A ceiling
   would trip on natural state-machine functions
   (`_run_query_fast_paths`, `_classify_parsed_tree`,
   `_avro_dict_to_arrow`) whose internal complexity is well-formed
   "small switch over a closed enum." Forcing those below rank-A
   would push the dispatch one layer further, producing strictly
   harder-to-read code without a complexity win at the call site.

4. **Use cognitive complexity instead.** SonarQube's cognitive
   complexity metric is more nuanced than cyclomatic for the
   "deep nesting vs. flat dispatch" axis. xenon doesn't support
   cognitive complexity; radon supports Halstead and maintainability
   index but not cognitive. Adopting cognitive complexity would
   require switching tools (SonarCloud was already declined per
   ADR 0035; pylint has it but isn't in the lint chain). The
   marginal value is real but the operational cost of adoption
   exceeds it.

5. **Ratchet `--max-modules` to B in the same PR.** Rejected
   per the rationale above. Module averages are aggregate; the
   right ratchet for them is a separate audit + per-module PR
   sequence if/when justified, not a side-effect of the
   per-function ratchet.

## References

* [ADR 0035](0035-code-quality-gates.md) — the foundational
  decision introducing the gates; this ADR is the third in the
  ratchet sequence (0035 → 0036 → 0041).
* [ADR 0036](0036-complexity-ratchet-to-c.md) — the C-ratchet
  ADR; remains in effect for the gate's promotion-to-required
  status and the bucket A/B/C taxonomy this ADR layers on top of.
  See ADR 0036's dated update at the bottom for the cross-reference.
* [radon documentation](https://radon.readthedocs.io/en/latest/) —
  rank-tier definitions (A: 1-5, B: 6-10, C: 11-20, D: 21-30,
  E: 31-40, F: ≥41).
* [xenon documentation](https://xenon.readthedocs.io/en/latest/) —
  CLI threshold semantics (`--max-absolute`, `--max-modules`,
  `--max-average`).
* Campaign PRs: [#90](https://github.com/jjviscomi/bqemulator/pull/90)
  through [#102](https://github.com/jjviscomi/bqemulator/pull/102)
  (excluding #97 lychee-retry hotfix and #101 lychee-max-redirects
  hotfix, which are unrelated).
* `AGENTS.md` "Pre-PR gate (mandatory)" — the contract this ADR
  extends; `make verify` runs `make quality-complexity` against
  the new ceiling.

## Update (2026-05-28): follow-up `--max-modules` ratchet in ADR 0042

This ADR's "Why we keep `--max-modules` at C" section held the
per-module-average ratchet open as "the right ratchet for them is a
separate audit + per-module PR sequence if/when justified, not a
side-effect of the per-function ratchet."

A focused audit immediately following this PR's merge tested that
assumption empirically: **the per-function C→B campaign drove module
averages down to ≤B as a side effect.** `xenon --max-modules B
src/bqemulator` exited 0 against `main` at HEAD `247fb60` (this PR's
own merge commit) with zero refactor work required. The audit found:

* **0 modules** with CC average above rank B (CC > 10).
* **14 modules** in the rank-B band (CC avg 6.01–10.00) — all
  passing the rank-B threshold; would become the audit list for a
  future B→A ratchet (out of scope).
* **120+ modules** at rank A (CC avg ≤ 5.0).

The mechanism: splitting branchy functions into dispatch tables +
helpers redistributed complexity weight across more (smaller,
lower-rank) functions, which lowered the per-module *average* even
where function count grew. The campaign's per-function bucket-A/B
patterns are also per-module-average favourable patterns.

[ADR 0042](0042-module-ceiling-ratchet-to-b.md) is the single-PR
config flip (`xenon --max-modules C → B`) that lands the
already-met threshold as the standing rule. ADR 0041 is not
superseded — it remains the per-function ratchet ADR; ADR 0042
documents the per-module-average ratchet as a complementary axis.
The "Why we keep `--max-modules` at C" section above is the
historical record of the held question; ADR 0042 is the resolution.
