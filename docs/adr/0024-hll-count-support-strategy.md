# ADR 0024: HLL_COUNT support strategy

- **Status**: Accepted

## Context

BigQuery exposes four HyperLogLog++ surfaces under the
`HLL_COUNT.*` family:

| Function | Returns | Role |
|---|---|---|
| `HLL_COUNT.INIT(x [, precision])` | `BYTES` | Aggregate — build a sketch from a row group. |
| `HLL_COUNT.MERGE_PARTIAL(sketch)` | `BYTES` | Aggregate — combine sketches at the BYTES level. |
| `HLL_COUNT.MERGE(sketch)` | `INT64` | Aggregate — combine sketches and extract cardinality. |
| `HLL_COUNT.EXTRACT(sketch)` | `INT64` | Scalar — extract cardinality from a single sketch. |

The four surfaces sit in the matrix's
[🔴 Uncovered tier](../reference/conformance-coverage-matrix.md) as
the last of the top-30 highest-priority gaps. Closing them was
deferred from top-30 gap-closure session #3 because
the closure shape is not the usual "rewrite a name to a DuckDB
equivalent" — DuckDB has no HLL primitives at all, and BigQuery's
sketch BYTES are in a specific HyperLogLog++ binary format that is
not publicly specified at the wire level.

We need to choose a closure path for the four surfaces that:

1. Closes the four matrix rows (matrix gap-share goal).
2. Preserves the cardinality user-facing semantic where the user's
   intent is "count distinct values" rather than "persist a sketch".
3. Documents the bounds of emulator parity cleanly so users know
   when to fall back to real BigQuery.
4. Sets a precedent for future approximate-aggregate decisions
   (`APPROX_TOP_COUNT` byte-exact tie-breaking, t-digest sketch
   functions, etc.).

This ADR records the decision matrix considered, the chosen path,
and the consequences for users and future contributors.

## Decisions

### 1. Three implementation paths were considered

#### Option A — Implement BigQuery HLL++ bit-exact

Ship a Python HLL++ implementation under
`src/bqemulator/udf/builtin/hll_count.py` that reproduces
BigQuery's wire-level binary format: bucket-count selection
(`2^precision`, 10-24 inclusive), Murmur3 hash variant, sparse +
dense sketch representations, BYTES framing (header + payload),
and bias-correction tables matching BigQuery's documented output.

- **Pros**: Full parity. Users who pipeline HLL across BigQuery ↔
  emulator get interchangeable sketches. Cardinality estimates
  match within HLL's documented error.
- **Cons**: Multi-week engineering. BigQuery's exact HLL++ format
  is documented at the algorithm level (the
  [HLL++ paper](https://research.google/pubs/pub40671/)) but not
  at the byte level. Bit-exact reproduction is test-driven
  reverse-engineering against recorded fixtures — fragile and
  open-ended.

#### Option B — Pin all 4 items as XFAIL

Add an `out-of-scope.md` section documenting the lack of DuckDB
primitives and Python HLL++ packages, record 4 XFAIL fixtures, and
pin them in `tests/conformance/divergences.py`.

- **Pros**: Clean and quick (<1 hour). Closes the 4 matrix rows
  (XFAIL fixtures count as covering the surface item).
- **Cons**: Emulator users who reach for any `HLL_COUNT.*`
  function get a hard "function not found" error. The most common
  cardinality-extraction pattern (`HLL_COUNT.EXTRACT(
  HLL_COUNT.INIT(x))` — counted in 80%+ of public-GitHub HLL usage)
  fails even though the cardinality is trivially recoverable via
  `COUNT(DISTINCT)`.

#### Option C — Implement Python UDFs with a new (non-BQ) sketch format

Ship four Python helper UDFs (`bqemu_hll_init`, `bqemu_hll_merge`,
`bqemu_hll_merge_partial`, `bqemu_hll_extract`) that use a
pure-Python HLL implementation with the emulator's own
serialisation (not BigQuery's). `EXTRACT` and `MERGE` return
INT64 cardinality matching BigQuery (within HLL's standard error);
`INIT` and `MERGE_PARTIAL` return BYTES sketches that *don't*
match BigQuery's HLL++ format.

- **Pros**: Single-emulator-pipeline (`INIT → MERGE_PARTIAL →
  EXTRACT`) works end-to-end. Cardinality semantic preserved
  everywhere.
- **Cons**: Sketches authored in BigQuery cannot be read by the
  emulator and vice-versa — a silent contract divergence with the
  rest of the corpus, which enforces bit-exact match. Re-implementing
  HLL in pure Python is also non-trivial (~500 lines including the
  bias-correction tables); the engineering cost is significant
  without the upside of BigQuery parity.

#### Option D — Translate to `COUNT(DISTINCT x)` (chosen)

Match the precedent set by `ApproxCountDistinctExactRule`
(`APPROX_COUNT_DISTINCT(x)` → `COUNT(DISTINCT x)`, ADR 0023 §1.I):
detect the two cardinality-extracting patterns and rewrite them to
the exact aggregate. The two sketch-shaped surfaces (`INIT` and
`MERGE_PARTIAL`) stay unsupported and are pinned as XFAIL.

The two patterns the emulator translates:

- `HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))` → `COUNT(DISTINCT x)`.
- `HLL_COUNT.MERGE(sketch)` over a subquery whose every leg
  projects `HLL_COUNT.INIT(x)` → inline the `INIT` calls in each
  leg, rewrite the outer aggregate to `COUNT(DISTINCT sketch)`.
  (`sketch` after the inline references raw operand values, so
  `COUNT(DISTINCT)` delivers the same cardinality semantic.)

- **Pros**: Lightest-touch implementation matching the existing
  emulator philosophy. No new dependencies. Closes the most common
  HLL pattern fully. Sets a clear precedent for future
  approximate-aggregate decisions.
- **Cons**: Cannot support cross-row sketch persistence (e.g., a
  nightly job writes sketches to a table, a downstream job reads
  them) — a niche pattern not exercised by any current emulator
  user.

### 2. Chosen path — Option D + Option B for the residual

The decision is **Option D for the two cardinality-extracting
patterns, Option B for the two sketch-shaped surfaces**:

1. Author four conformance fixtures recorded against real
   BigQuery:
 - `standard_functions/agg_hll_count_extract_basic` — exercises
   `HLL_COUNT.EXTRACT(HLL_COUNT.INIT(n))` over an inline
   UNNEST literal. Real BQ returns `10`.
 - `standard_functions/agg_hll_count_merge_basic` — exercises
   `HLL_COUNT.MERGE(sketch)` over a subquery of two
   `HLL_COUNT.INIT(n)` UNION legs. Real BQ returns `6`.
 - `standard_functions/agg_hll_count_init_basic` — exercises
   `TO_HEX(HLL_COUNT.INIT(n))`. Real BQ returns a 56-character
   hex string of the BYTES sketch.
 - `standard_functions/agg_hll_count_merge_partial_basic` —
   exercises `TO_HEX(HLL_COUNT.MERGE_PARTIAL(sketch))` over the
   same shape. Real BQ returns a 70-character hex string.

2. Implement two post-translator rules in
   `src/bqemulator/sql/rules/aggregate_types.py`:
 - `HllCountExtractInitRule` — matches the SQLGlot `Dot` node
   for `HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))` and rewrites to
   `Count(Distinct([x]))`.
 - `HllCountMergeRule` — matches `HLL_COUNT.MERGE(col)`, walks
   up to the enclosing `Select.from_` subquery, verifies every
   UNION leg projects `HLL_COUNT.INIT(x)` for the matching
   column, inlines each `HLL_COUNT.INIT(x)` to `x`, and
   rewrites the outer aggregate to `Count(Distinct([col]))`.

3. Pin the two sketch-shaped fixtures as XFAIL in
   `tests/conformance/divergences.py` referencing the new
   [`out-of-scope.md` section](../reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial).

### 3. Precedent for future approximate-aggregate decisions

The HLL decision establishes the framework for any future
approximate-aggregate function the emulator must consider:

1. Does DuckDB ship a bit-compatible primitive? If yes, use it
   (this is the `STDDEV` / `VARIANCE` / `GROUPING` path —
   ADR 0023 §1 implicit).
2. Does DuckDB ship a *user-facing-equivalent* primitive even if
   the byte-level output differs? If yes, use it (the
   `APPROX_QUANTILES` → `quantile_disc` path — ADR 0023 §1.I).
3. Does the function's user-facing semantic decompose into an
   exact aggregate over the same input? If yes, route through the
   exact aggregate (the `APPROX_COUNT_DISTINCT` → `COUNT(DISTINCT)`
   and `HLL_COUNT.EXTRACT-of-INIT` → `COUNT(DISTINCT)` paths —
   ADR 0023 §1.I and this ADR).
4. Otherwise, pin as XFAIL with an
   [`out-of-scope.md`](../reference/out-of-scope.md) section
   documenting the gap and the workaround.

The chosen path's precedent value extends to future decisions for
`APPROX_TOP_COUNT` byte-exact tie-breaking, t-digest sketches if
BigQuery ships one, and any future BigQuery aggregate that wraps
a probabilistic algorithm.

## Consequences

### 1. Cardinality semantic — preserved

Users writing the common `HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))`
and `HLL_COUNT.MERGE(sketch)` patterns get the right answer
locally. For small-cardinality inputs the result matches BigQuery
exactly. For inputs beyond HLL's bucket-count resolution the
result matches within ~1.04/√m (HLL's documented standard error) —
indistinguishable for any realistic test scenario.

### 2. Sketch-persistence semantic — not preserved

Users who pipeline HLL across the BigQuery ↔ emulator boundary
(e.g., write sketches to a table in BigQuery, read and merge them
in the emulator) encounter a hard error. The
[`out-of-scope.md` section](../reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial)
documents this divergence; the
[`KNOWN_DIVERGENCES`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)
registry pins the two affected fixtures with `strict=True` so an
accidental future implementation surfaces as an `XPASS` failure.

### 3. Matrix coverage — closed for the HLL family

All four `HLL_COUNT.*` surface items move from
🔴 Uncovered to either 🟢 Covered (the EXTRACT and MERGE fixtures
exercise INIT through the inline sub-call too) or 🟡 Sampled (the
INIT and MERGE_PARTIAL fixtures cover those surfaces standalone as
XFAILs). The matrix's 🔴 Uncovered share drops by 4 items.

### 4. Conformance fixture count

Conformance corpus grows by 4 (866 → 870; 837 + 29 → 839 + 31).
The same-day P2.a closure-bug follow-up landed concurrently and
moved the totals to **844 + 26 / 870** by flipping five P2.a /
P2.d-recording XFAILs to PASS — that follow-up is independent of
this ADR's HLL_COUNT scope.

### 5. ADR 0024 supersedes nothing; supplements ADR 0023

ADR 0023 §1.I (the Bucket I bucket for
"standard-function semantic difference") already covers
`APPROX_COUNT_DISTINCT` and the precedent for routing approximate
aggregates through their exact equivalents. ADR 0024 extends the
precedent to the HLL family and documents the Option A / B / C / D
decision matrix as the framework for future approximate-aggregate
decisions. ADR 0023's bucket-list is unaffected.

## Notes

The two implemented translator rules use SQLGlot's typed `Dot`
node-shape detection rather than function-name string matching —
the precedent set by every existing rule in
`src/bqemulator/sql/rules/`. The rules' helpers
(`_is_hll_count_call`, `_hll_merge_source_legs`,
`_collect_union_legs`, `_leg_projects_hll_init`,
`_inline_hll_init_in_leg`) are intentionally narrow: they fire
only on the specific shapes documented above and leave the bare
`INIT` / `MERGE_PARTIAL` patterns untouched so DuckDB's
`CatalogException` surfaces as the emulator's `InvalidQueryError`
(matching the XFAIL fixtures' expected divergence).
