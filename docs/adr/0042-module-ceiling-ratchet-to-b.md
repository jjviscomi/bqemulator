# ADR 0042: Per-module-average ratchet to rank B (no-refactor follow-on)

- **Status**: Accepted

## Context

The cyclomatic-complexity gate has three independent thresholds
([ADR 0035](0035-code-quality-gates.md) §"Wiring"):

| `xenon` flag | What it caps | Pre-this-ADR value |
|---|---|---|
| `--max-absolute` | The worst-ranked single function | **B** (per [ADR 0041](0041-complexity-ratchet-to-b.md)) |
| `--max-modules` | The highest module-average CC | **C** |
| `--max-average` | The project-wide CC average | **A** |

[ADR 0036](0036-complexity-ratchet-to-c.md) and [ADR 0041](0041-complexity-ratchet-to-b.md)
ratcheted `--max-absolute` from E → C → B through two campaigns. The
per-function refactors were the load-bearing work; the gate flips
were the recognition step.

ADR 0041's "Why we keep `--max-modules` at C" section explicitly held
the module-average ratchet open as the *next* question, with the
proviso that "the right ratchet for them is a separate audit +
per-module PR sequence if/when justified, not a side-effect of the
per-function ratchet." It anticipated a fresh refactor campaign
analogous to PR-1 through PR-11, scoped per-subsystem, before any
flip of `--max-modules`.

## Audit

A focused audit against `main` HEAD `247fb60` (the ADR 0041 merge
commit) tested ADR 0041's anticipation. The audit query was simple:
**without any new refactor work, does `xenon --max-modules B
src/bqemulator` exit 0?**

The dry-run result:

```
$ xenon --max-absolute B --max-modules B --max-average A src/bqemulator
$ echo $?
0
```

The codebase already complies. The per-module distribution against
the proposed threshold:

| Avg CC band | Rank | Module count | Notes |
|---|---|---|---|
| > 10.00 | C+ | **0** | None — confirmed by xenon exit 0 |
| 6.01 – 10.00 | B | **14** | All passing the rank-B threshold (B is inclusive of CC 10) |
| ≤ 5.00 | A | **120+** | Vast majority |

The 14 rank-B modules:

| Avg CC | Worst | Blocks | Module |
|---|---|---|---|
| 10.00 | 10 | 1 | `sql/rewriter/sha512.py` |
| 8.40 | 10 | 5 | `udf/types.py` |
| 7.00 | 10 | 5 | `versioning/time_travel.py` |
| 7.00 | 8 | 4 | `sql/rewriter/string_helpers.py` |
| 6.75 | 9 | 4 | `sql/rewriter/unnest_struct.py` |
| 6.67 | 10 | 3 | `jobs/orc_reader.py` |
| 6.00 | 10 | 2 | `sql/rewriter/range_sessionize.py` |
| 6.00 | 9 | 4 | `streaming/strategies/buffered.py` |
| 5.75 | 10 | 4 | `sql/rewriter/create_table_schema_ctas.py` |
| 5.67 | 10 | 9 | `storage/type_map.py` |
| 5.67 | 9 | 3 | `sql/catalog_schema.py` |
| 5.20 | 8 | 10 | `sql/rewriter/specialized_types.py` |
| 5.20 | 8 | 5 | `sql/rewriter/timestamp_iso_helpers.py` |
| 5.14 | 10 | 7 | `sql/translator.py` |

These are the "future B→A audit list" — out of scope for this ADR.

### Why no refactor work is needed

The per-function ratchet campaigns (PR-1 through PR-11, closing
~60 rank-C functions) drove module averages down to ≤B **as a side
effect**. The mechanism:

* Splitting a CC-15 dispatch function into a CC-3 dispatch loop +
  N CC-2 handlers redistributes complexity weight across more
  (smaller, lower-rank) functions.
* The new helpers land at rank A almost universally (the C→B
  campaign observed this empirically — ~150-200 new helpers added
  across PR-1 through PR-11, overwhelmingly rank A).
* The per-module *average* drops because the denominator (function
  count) grows faster than the numerator (sum of complexities).

ADR 0041's anticipation that the module ratchet would need its own
campaign was correct *in theory* — module averages aren't the same
metric as per-function ceilings, and a codebase could plausibly
exist where per-function refactors didn't move the per-module
average enough. In *this* codebase, the bucket-A/B refactor patterns
that worked for the per-function ratchet happened to also work for
the per-module-average ratchet.

## Decision

1. **Tighten `--max-modules` to rank B** in the Makefile gate:

   ```
   xenon --max-absolute B --max-modules B --max-average A src/bqemulator
   ```

   The project-wide `--max-average` stays at A (currently 3.06, well
   below the 5.0 rank-A ceiling). All three xenon thresholds now sit
   one ratchet step tighter than they did before the C→B work
   started.

2. **No refactor work.** The audit verified the codebase already
   complies. This ADR's PR is a Makefile flip + workflow comment
   updates + this ADR + an additive `Update` section on ADR 0041 +
   an mkdocs nav entry. Total diff is ~10 lines of config + ~150
   lines of docs.

3. **Defer `--max-modules B → A` to a future audit.** The 14 rank-B
   modules listed above would need refactoring before a `--modules A`
   ratchet could land. Several look genuinely irreducible — notably
   `sql/rewriter/sha512.py` is a single CC-10 function (SHA-512
   software implementation; the unavoidable branch count is the
   complexity), and `udf/types.py` is type-conversion dispatch
   already at the natural floor. A B→A campaign would likely
   produce the campaign's first bucket-C exclusions (the
   "irreducible domain complexity" exit ADR 0036 anticipated but
   neither ratchet has yet needed). That's a deliberate future
   audit, not a side-effect ratchet.

4. **ADR 0041 stays in effect.** Like ADR 0036 under ADR 0041,
   ADR 0041 remains the authoritative ADR for the per-function
   `--max-absolute` ceiling. ADR 0042 layers a tighter
   `--max-modules` on top — different axis, complementary ratchet.

## Consequences

### Positive

* The per-module-average gate now defends against drift the same
  way the per-function gate does. A future PR that adds a
  rank-C-average module fails CI loudly instead of merging silently.
* The "campaign side-effect" pattern is documented as a real
  phenomenon — future ratchet audits should check the side-effect
  state of orthogonal thresholds before assuming a fresh campaign
  is needed.
* The three xenon thresholds are now visually consistent
  (`B / B / A` is easier to read at a glance than `B / C / A`),
  which makes drift more legible during code review.

### Negative

* The gate is now strict enough that adding *any* CC-10 function to
  a single-block module risks tipping the module's average over the
  B boundary (since the average is the only function's CC). This
  matters for the 14 rank-B modules listed above whose averages
  already sit at or near the boundary. Mitigation: the same
  bucket-A/B refactor patterns that worked across the C→B campaign
  work here — split a CC-10 function into a CC-3 dispatch + 3 CC-3
  helpers and the module average drops to ~3.
* A future contributor who lands a CC-10 helper in
  `sql/rewriter/sha512.py` (which currently averages 10.00 with
  exactly 1 block) would fail the gate even though the helper
  itself is rank-B-clean. Mitigation: the small-single-purpose-file
  pattern that `sha512.py` exemplifies is rare — most files in the
  codebase carry multiple helpers and have headroom against the
  module-average.

### Neutral

* No production runtime impact. Pure config + docs ratchet.
* The `make verify` chain's complexity step continues to run xenon;
  the only difference is `--max-modules B` vs `C`. Local + CI cost
  is unchanged.
* CHANGELOG entry deferred to release time per the project's
  [release-time-authored CHANGELOG policy](https://github.com/jjviscomi/bqemulator/blob/main/CHANGELOG.md).
  This ADR is the authoritative record of the ratchet until the next
  release synthesises a CHANGELOG bullet from `git log`.

## Alternatives considered

1. **Run a per-module B→A refactor campaign first, then ratchet
   `--max-modules` in one big step to A.** Rejected as scope-creep
   — the data already supports the B-step landing today with zero
   refactor work. Conflating "free ratchet that's already met" with
   "campaign that requires real refactor work" loses both the
   defensive value of the cheap ratchet (which would wait on the
   slow campaign) and the audit clarity (the B→A campaign would
   need its own retrospective ADR, which the B-step ratchet doesn't
   need to block).

2. **Skip `--max-modules` and ratchet `--max-average A → ??`
   instead.** Not possible — xenon doesn't expose sub-rank
   ceilings. The project-wide average is already at the tightest
   rank xenon supports. Tightening "more" would require either
   forking xenon or switching to a different tool (radon's `cc -a
   --total-average` reports the number; xenon doesn't gate on
   sub-rank numeric thresholds).

3. **Fold this ratchet into a future release-related PR.** Rejected
   — the audit signal is independent of release cadence, and the
   defensive value of the new threshold compounds with every PR
   merged in the meantime. Better to land the cheap defensive
   ratchet immediately than wait for an unrelated trigger.

4. **Run the same per-subsystem multi-PR pattern as ADR 0041's
   campaign for the module-ceiling ratchet, even though the
   per-PR diff is empty.** Rejected — the campaign pattern's
   value was *review surface* (no PR exceeds N files / M LOC of
   refactor work). With zero refactor work required, the pattern
   produces a sequence of empty PRs each doing nothing. The
   single-PR shape this ADR adopts matches the actual work shape.

## References

* [ADR 0035](0035-code-quality-gates.md) — the foundation ADR
  documenting the three independent xenon thresholds this ADR
  ratchets one of.
* [ADR 0036](0036-complexity-ratchet-to-c.md) — the first
  per-function ratchet (E → C). Established the bucket A/B/C
  taxonomy.
* [ADR 0041](0041-complexity-ratchet-to-b.md) — the second
  per-function ratchet (C → B). Anticipated this ADR under
  "Why we keep `--max-modules` at C"; see ADR 0041's dated
  update at the bottom for the cross-reference.
* [radon documentation](https://radon.readthedocs.io/en/latest/) —
  rank-tier definitions (A: 1-5, B: 6-10, C: 11-20, D: 21-30,
  E: 31-40, F: ≥41).
* [xenon documentation](https://xenon.readthedocs.io/en/latest/) —
  CLI threshold semantics (`--max-absolute`, `--max-modules`,
  `--max-average`).
* `AGENTS.md` "Pre-PR gate (mandatory)" — the contract this ADR
  extends; `make verify` runs `make quality-complexity` against
  the new ceiling.
