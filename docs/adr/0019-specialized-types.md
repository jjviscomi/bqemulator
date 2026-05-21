# ADR 0019: Specialized types (GEOGRAPHY / RANGE / INTERVAL) backend choices

- **Status**: Accepted (Decision #5 superseded by
  Phase 11 scope-expansion #15 — `RANGE_SESSIONIZE` now in scope
  for v1.0; see [ADR 0023](0023-conformance-divergence-baseline.md)
  scope-expansion #15 closure note for the implementation
  approach.)
- **Date**: (last revised — Decision #5
  superseded)

## Context

Phase 9 adds BigQuery's `GEOGRAPHY`, `RANGE<T>`, and `INTERVAL` types
to the emulator. None of the three is a 1:1 DuckDB primitive, and
each has multiple plausible backings. The choices below lock the
storage shape, the SQL translation strategy, and the boundary between
in-scope and out-of-scope behaviour for v1.0.0.

## Decisions

### 1. GEOGRAPHY → DuckDB GEOMETRY via the `spatial` extension

Options considered:

1. **DuckDB `spatial` extension (selected).** Implements ~155 `ST_*`
   functions, ingests WKT / GeoJSON / WKB, integrates with DuckDB's
   storage engine, and ships with the official `duckdb` Python
   wheel. Trade-off: planar (Cartesian) geometry — distance / area /
   perimeter values diverge from BigQuery's spheroidal results at
   continental scales.
2. **Bundled shapely + custom storage.** Reject — adds a heavyweight
   pure-Python geometry library that we'd have to bridge into
   DuckDB's storage on every read/write. The emulator's storage path
   becomes substantially more complex.
3. **PostGIS via embedded postgres.** Reject — out of project
   architecture; would require shipping a second database engine.

DuckDB spatial is **required**, not best-effort: the engine fails
fast at startup if `INSTALL spatial` / `LOAD spatial` fails. Phase 0
shipped it as best-effort because no SQL surface depended on it;
Phase 9 wires every `ST_*` rule onto the extension, so falling back
to a "no spatial" mode would silently produce wrong results.

### 2. RANGE<T> → STRUCT<start T, "end" T>

Options considered:

1. **Custom DuckDB UDT.** Reject — DuckDB does not expose user-
   defined types in a way that supports column-typed storage today,
   and a UDT shim would have to re-implement every range function.
2. **Native DuckDB RANGE (none exists).** Reject.
3. **STRUCT with named fields (selected).** Two-field STRUCT
   (`start` / `end`) mirrors the projection names BigQuery exposes
   (`r.start`, `r.end`). DDL quotes `"end"` because it's a keyword.

The constructor `RANGE(a, b)` is rewritten in the pre-translator pass
(`sql/rewriter/specialized_types.py`) to a BigQuery STRUCT literal
(`STRUCT(a AS \`start\`, b AS \`end\`)`) so SQLGlot transpiles it to
DuckDB's `{...}` struct constructor. Without that rewrite SQLGlot's
DuckDB-side parser collapses two-argument `RANGE(a, b)` into a
`GenerateSeries` AST node — the same shape it uses for
`GENERATE_ARRAY(a, b)` — and the post-translator pass cannot
distinguish the two.

### 3. INTERVAL → DuckDB INTERVAL

Options considered:

1. **Native DuckDB INTERVAL (selected).** Same component model as
   BigQuery (months / days / microseconds). Most BigQuery syntactic
   forms parse directly under DuckDB's grammar.
2. **Decimal-of-microseconds.** Reject — loses the month component
   (months are not a fixed number of microseconds).

The exception is BigQuery's compound literal
`INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND`, which DuckDB's parser
rejects. We rewrite it before SQLGlot transpile, parsing the literal
in Python (`types/interval.parse_interval_literal`) and emitting the
equivalent sum of single-unit intervals.

### 4. JUSTIFY_* synthesised from primitives

DuckDB has no `justify_hours` / `justify_days` / `justify_interval`
scalar functions. The translator emits a normalisation expression
built from `to_months` / `to_days` / `to_hours` / `to_minutes` /
`to_microseconds` plus `// 24` (hours into days) and `// 30` (days
into months) integer-division pulls. The expression is verbose but
correct (verified against PostgreSQL's documented JUSTIFY semantics)
and lives behind the SQL rule — users see only the
BigQuery-grammar `JUSTIFY_*` call.

### 5. RANGE_SESSIONIZE — *originally* out of scope, superseded

**Historical decision (Phase 9 ship).**
`RANGE_SESSIONIZE` was a table-valued function whose implementation
requires rewriting *table references* in the FROM clause to inject
window functions for session attribution. The existing rewriter
pipeline operated on expressions, not table sources. Bridging that
gap was non-trivial and orthogonal to the Phase 9 ship criterion.
The TVF was listed in `docs/reference/out-of-scope.md` with a
manual workaround using window functions; the SQL rule rejected
the call with a clean `UnsupportedFeatureError`.

**Closure (Phase 11 scope-expansion #15).**
After Bucket J's closure expanded the rewriter machinery to cover
function-shape rewrites and Bucket G's closure established the
canonical `STRUCT("start" T, "end" T)` → RANGE wire-format path,
the implementation became tractable. The closure ships a new
pre-translator at
[`src/bqemulator/sql/rewriter/range_sessionize.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/range_sessionize.py)
that rewrites every
`RANGE_SESSIONIZE(TABLE <ref>, '<range_col>', [<part_cols>] [,
'<sessionize_option>'])` call into a windowed gaps-and-islands
subquery. The rewrite operates on the raw SQL text because
SQLGlot's BigQuery parser rejects the `TABLE <ref>` keyword in
TVF arguments. Mode dispatch matches BigQuery's documented
semantics: `MEETS` (default) and the `OVERLAPS_OR_MEETS` alias
use strict `>` for the new-session predicate so touching ranges
share a session; `OVERLAPS` uses `>=` so touching ranges form
separate sessions. A second pass `_rewrite_range_data_types` in
`specialized_types.py` converts `RANGE<T>` column-type /
non-literal-CAST references to `STRUCT<\`start\` T, \`end\` T>`
so DDL like `CREATE TABLE t (col RANGE<DATE>)` survives the
DuckDB parser. The `RangeSessionizeRejectRule` post-translate
rule is removed. Three new conformance fixtures recorded against
real BigQuery (`range_sessionize_basic`, `range_sessionize_grouped`,
`range_sessionize_overlaps_option`) all pass. See
[ADR 0023](0023-conformance-divergence-baseline.md) scope-expansion
#15 closure note for the full implementation walk-through.

### 6. REST wire format

* `GEOGRAPHY`: scalar type, no sub-fields. Inbound values are WKT
  strings; outbound rows carry WKT (converted from WKB by a lazy
  in-process DuckDB connection in `types/geography.wkb_to_wkt`).
* `INTERVAL`: scalar type, no sub-fields. Inbound and outbound use
  the BigQuery-canonical `Y-M D H:M:S[.ffffff]` string.
* `RANGE`: requires a `rangeElementType: { type: "DATE" | "DATETIME"
  | "TIMESTAMP" }` sub-field. Matches the shape documented in
  the [BigQuery REST `TableFieldSchema` reference](https://cloud.google.com/bigquery/docs/reference/rest/v2/tables)
  and confirmed against the
  [google-cloud-go schema.go](https://github.com/googleapis/google-cloud-go/blob/main/bigquery/schema.go)
  before locking.

### 7. TIMESTAMP wire format (fixed in-pass)

While exercising INTERVAL arithmetic end-to-end (which projects
TIMESTAMP results to the BigQuery Python client), Phase 9 uncovered
a latent Phase-1 bug: the emulator emitted TIMESTAMP values as the
human-readable `"YYYY-MM-DD HH:MM:SS.ffffff UTC"` string, but the
official Python client (`_timestamp_from_json`) calls `int(value)`
on the field, expecting microseconds-since-epoch. The fix lives in
`storage/arrow_bridge._format_bq_value` and is covered by the
updated arrow-bridge test plus the new Phase 9 integration tests.
Documented here so future audits don't think it was a Phase 9
regression introduced by the new path.

## Consequences

- **Positive.** Spatial / RANGE / INTERVAL queries pass against a
  live container in all four client languages.
- **Positive.** The rewriter is composable: spatial / range / interval
  rules all live behind the same post-order rule-application pass
  that ADR 0018's row-access rewriter uses.
- **Negative.** Distance / area / perimeter on GEOGRAPHY values
  diverge from real BigQuery at continental scales because DuckDB's
  GEOMETRY is planar. Acceptable for an integration-test target;
  documented in the guide and the out-of-scope file. The
  Bucket H conformance closure widened the conformance-fixture
  pinning to *derived* shape outputs at smaller scales too —
  ``ST_Centroid`` of a small polygon (planar centroid sits at the
  exact midpoint where the spheroidal centroid is slightly offset),
  ``ST_Intersection`` of two polygons (planar straight edges vs.
  spheroidal geodesic curves bulging by ~1e-3 degrees), and the
  predicate-form ``ST_DWithin`` (the truth value flips when the
  planar and spheroidal distances happen to straddle the
  threshold). These fixtures
  (``st_centroid_polygon`` / ``st_intersection_polygons`` /
  ``st_dwithin_no``) sit under the same out-of-scope anchor as the
  continental measurements.
- **Positive (resolved).** `RANGE_SESSIONIZE` was
  originally listed as a negative consequence under this ADR; the
  TVF is now supported as of Phase 11 scope-expansion #15. See
  Decision #5 above for the closure approach.
- **Negative.** The pre-translator runs a second SQLGlot parse on
  every query that contains `INTERVAL... TO...` or `RANGE(...)`
  syntax. The short-circuit avoids the parse when neither is
  present.
