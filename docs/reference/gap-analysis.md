# Emulator-vs-BigQuery gap analysis

Comprehensive catalogue of every known difference between bqemulator
and real Google BigQuery. Sources, in priority order:

1. **[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)** — **15 fixtures** pinned to `xfail(strict=True)`
   (was 199 at slice-2 close). Every remaining entry is rooted in
   either [ADR 0019](../adr/0019-specialized-types.md)
   (spheroidal-vs-planar) or [out-of-scope.md](out-of-scope.md).
   **All ten ADR 0023 buckets (A–J) and the three reconsidered
   scope expansions (#15 / #17 / #18) closed by.** The
   → v1-confidence-plan workstreams (P2.a–g,
   P3.a, P3.b, P4.a, P7.a–c) closed every remaining
   closure-eligible divergence; the surviving 15 are permanent
   design decisions with no v1.0 closure plan.
2. **[`out-of-scope.md`](out-of-scope.md)** — **19 features** explicitly excluded from
   v1.0.0 with rationale. Growth since slice-2:
 - +`BIGNUMERIC literals with > 28 integer digits` (Bucket I close).
 - +`HLL sketch binary format (HLL_COUNT.INIT / MERGE_PARTIAL)`
   (session #3b,; ADR 0024).
 - +`DBSCAN clustering (ST_CLUSTERDBSCAN)` (session #3d).
 - 2 sections removed by P7.c — `#ingestion-time-partition-pseudo-columns`
   and `#st_maxdistance-not-yet-implemented` — once narrow translator
   rules closed those gaps.
3. **Untested-in-conformance surfaces** — capabilities the emulator
   ships but the conformance corpus doesn't yet exercise (because
   they aren't SQL, are non-deterministic, or are exercised in
   adjacent tiers).

A "gap" in this document means *anything* a user might rely on in
real BigQuery that the emulator does not currently provide
identically. The catalogue is intentionally exhaustive — most
entries have a small, named closure path; a few are permanent v1.0
exclusions.

## At a glance

| Source | Count | What it is |
|---|---|---|
| **ADR 0023 divergence buckets A–J** | **0 fixtures** (was 194 at slice-2 close; all ten buckets A–J closed) | Documented runtime behavioural deltas surfaced by the slice-2 conformance corpus. Each had a root cause and a slice-named closure plan; all have now landed. |
| **ADR 0019 spheroidal GEOGRAPHY** | **11 fixtures** | Permanent v1.0 divergence (S2-sphere semantics for the helper rules vs planar / no-spherical fallback for the rest). The 3 remaining buffer fixtures from P2.g + 8 pre-existing surfaces (4 `st_asgeojson_*` interpolation, `st_centroid_polygon`, `st_intersection_polygons`, `st_buffer_continental`, `st_asbinary_point`). |
| **ADR 0024 HLL++ sketch binary format** | **2 fixtures** | `agg_hll_count_init_basic` + `agg_hll_count_merge_partial_basic` — BigQuery's HLL++ sketch BYTES format is undocumented. The 2 sibling EXTRACT/MERGE surfaces pass cleanly via `COUNT(DISTINCT)`. |
| **`out-of-scope.md` locked exclusions (fixture-bearing)** | **2 fixtures** | `bound_bignumeric_max` (DuckDB DECIMAL(38) cap on 39-integer-digit literals; `out-of-scope.md#bignumeric-literals-with-39-integer-digits`) + `row_access/caller_information_schema_visibility` (IAM-fundamental; `out-of-scope.md#iam-enforcement`). |
| **`out-of-scope.md` locked exclusions (no-fixture)** | **17 features** | Surfaces explicitly excluded from v1.0 with rationale but with no corpus fixture (BQML, BI Engine, billing, scheduling, multi-region, etc.). |
| **Conformance coverage gaps** | **~12 surfaces** | Shipped emulator features not yet exercised by the conformance corpus (covered in other test tiers). |

Total **15** documented runtime divergences (was 199 at slice-2
close) — 11 ADR 0019 spheroidal + 2 ADR 0024 HLL++ + 2
out-of-scope.md fixture-bearing entries — plus **19** total
locked exclusions in `out-of-scope.md` (17 no-fixture + 2
fixture-bearing) + ~12 untested-in-conformance surfaces.

## 1. ADR 0023 — Runtime behavioural divergences (0 open entries — all closed)

Each bucket has full root-cause analysis in
[ADR 0023](../adr/0023-conformance-divergence-baseline.md); per-fixture rationale strings live in
`divergences.py`. Summary:

### Bucket A — REPEATED-row wire-format shape — ✅ Closed

* **Status**: closed in the parity-closure session
  (16 direct XPASSes + 8 incidental closures from buckets I/J;
  6 stragglers re-categorised — `generate_range_array_date` → G,
  `agg_array_agg_ordered`, `json_lax_{bool,int64,string}` → J,
  `json_to_json_array` → I).
* **Symptom (historical)**: any fixture with a `REPEATED` (array)
  column triggered
  `TypeError: list indices must be integers or slices, not str`
  deep in the `google-cloud-bigquery` row deserialiser.
* **Root cause (historical)**: the REST schema renderer in
  `bqemulator.jobs.executor.build_response_schema` produced
  `{type: "RECORD", mode: "NULLABLE"}` for Arrow list-typed
  columns; BigQuery's wire shape is
  `{type: <element_type>, mode: "REPEATED"}`. Secondary bug: NULL
  Arrow values for REPEATED columns rendered as `null` instead of
  `[]`.
* **Closure**: schema-and-row renderer change in
  `src/bqemulator/jobs/executor.py` (build_response_schema)
  and `src/bqemulator/storage/arrow_bridge.py` (_format_bq_value).
  See ADR 0023 §1.A closure note for full details.

### Bucket B — Numeric type promotion FLOAT64 ↔ NUMERIC — ✅ Closed

* **Status**: closed in the parity-closure session (all
  22 fixtures moved from XFAIL to PASS, plus 12 incidental
  XPASSes across Buckets H and I).
* **Historical symptom**: schema-level type mismatch
  (`expected='NUMERIC' actual='FLOAT'` or reverse), and `Conversion
  Error` for wide-NUMERIC literals exceeding DuckDB's default
  ``DECIMAL(18, 3)``.
* **Historical root cause**: BigQuery's literal-type inference
  treats a decimal-looking literal (`3.25`) as FLOAT64; DuckDB
  treats it as DECIMAL. Same drift for aggregates over NUMERIC:
  BigQuery keeps NUMERIC; DuckDB's ``AVG`` always promotes to
  DOUBLE. ``SUM(BIGINT)`` and ``COUNT_IF`` promote to HUGEINT
  which Arrow encoded as decimal128(38, 0) and the renderer
  surfaced as NUMERIC. ``SIGN(INT)`` returns TINYINT (Arrow int8)
  which fell through to the STRING fallback. ``DATE_TRUNC(date,
  QUARTER/WEEK)`` returned TIMESTAMP instead of DATE, and WEEK
  truncated to Monday rather than the BigQuery-default Sunday.
  ``PARSE_NUMERIC`` and ``PARSE_BIGNUMERIC`` had no DuckDB
  analogue. ``BIGNUMERIC '…'`` typed literals lacked a rewrite
  path that preserved the BIGNUMERIC type tag without sacrificing
  integer-digit capacity.
* **Fixtures closed**: `select_float64_literal`,
  `select_bignumeric_literal`, `select_avg`, `select_count_if`,
  `agg_countif`, `agg_sum_int`, `num_round`,
  `num_round_decimals`, `num_ceil`, `num_floor`, `num_trunc`,
  `num_sign_{pos,neg,zero}`, `parse_numeric_basic`,
  `parse_bignumeric_basic`, `dt_date_trunc_quarter`,
  `dt_date_trunc_week`, `math_round_half_away`, `math_round_neg`,
  `rw_window_rows_between`, `tpch_q1`.
* **Closure approach**: seven coordinated translator fixes plus a
  new catalog-schema-aware ``annotate_types`` pass. (1) A new
  `src/bqemulator/sql/rewriter/decimal_literals.py` rewrites bare
  decimal literals (`3.25`) to scientific notation (`3.25e0`) so
  DuckDB types them as DOUBLE. (2) `_arrow_type_to_bq_type` in
  `src/bqemulator/jobs/executor.py` widens to every integer
  width via ``pa.types.is_integer`` (fixes TINYINT from
  ``SIGN(INT)``); the decimal branch inspects Arrow scale and
  routes DECIMAL with ``scale > 9`` to BIGNUMERIC. (3) The
  `_resolve_bq_type` override reads ``bqemu.duckdb_type``
  metadata and surfaces HUGEINT as INTEGER. (4) New SQLGlot
  rules in `src/bqemulator/sql/rules/iso_date_parts.py` handle
  ``DATE_TRUNC(date, QUARTER)`` (cast to DATE) and
  ``DATE_TRUNC(date, WEEK)`` (Sunday-start truncation via
  ``CAST(d - INTERVAL DAYOFWEEK(d) DAY AS DATE)``). (5) A new
  `src/bqemulator/sql/rules/numeric_types.py` adds PARSE_NUMERIC
  (``CAST AS DECIMAL(38, 9)``) and PARSE_BIGNUMERIC
  (UDF-routed) rules. (6) A new ``bqemu_to_bignumeric`` Python
  UDF in `src/bqemulator/sql/builtin_udfs.py` returns
  ``DECIMAL(38, 10)`` so the scale-marker rule fires; the
  pre-translator at
  `src/bqemulator/sql/rewriter/numeric_literals.py` is now
  scale-aware (≥ 10 fractional digits → direct CAST, ≤ 9 →
  UDF). (7) A new `src/bqemulator/sql/catalog_schema.py` helper
  builds a per-table schema dict; the executor passes it to
  ``SQLTranslator.translate(bq_sql, schema=…)`` and the
  translator runs ``qualify`` + ``annotate_types`` so the new
  `AvgDecimalRule` in
  `src/bqemulator/sql/rules/aggregate_types.py` can wrap
  ``AVG(decimal_col)`` in ``CAST AS DECIMAL(38, 9)``. Windowed
  forms dispatch on the parent ``Window`` so the cast surrounds
  the entire ``AVG(…) OVER (…)`` expression. See ADR 0023 §1.B
  closure note for full details.
* **Incidental closures**: ``st_geogfromtext_multipoint``,
  ``st_isring_line``, ``st_npoints_line``,
  ``st_numpoints_polygon``, ``st_pointn_line`` (Bucket H —
  narrow-width integer renderer fix), and
  ``agg_bit_count_scalar``, ``agg_sum_empty``,
  ``agg_sum_null_col``, ``empty_array_aggsum``,
  ``rw_case_in_aggregate``, ``rw_session_count``,
  ``str_regexp_instr`` (Bucket I — HUGEINT / narrow-integer
  renderer fix).

### Bucket C — Wildcard table expansion — ✅ Closed

* **Status**: closed in the parity-closure session
  (all 8 fixtures XPASSed and were removed from
  `divergences.py`).
* **Symptom (historical)**: `FROM \`<dataset>.events_*\`` failed
  with `Catalog Error: Table with name events_* does not exist`.
* **Root cause (historical)**: the original wildcard-rewriter
  predicate looked only at the trailing identifier shape and did
  not engage when the reference was fully-qualified with a project
 + dataset prefix (conformance fixtures expand
   `${DATASET}.events_*` to `<project>.<temp>.events_*`).
   Compounding factors surfaced in the closure: `re.search`
   expanded only the first wildcard in a query (so self-joins
   failed), the catalog cache never sees DDL-created shards (so
   even the 2-part fix would have returned an empty match set),
   and DuckDB does not dedupe duplicate column names on a
   self-join's projection.
* **Closure**: widened the wildcard-expander predicate in
  `src/bqemulator/sql/rewriter/wildcard_expander.py` to match
  every BigQuery reference shape (1-/2-/3-part, with or without
  backticks, hyphenated project ids via `[\w-]`), switched the
  expander to `re.sub` so every occurrence expands (preserving
  any explicit `AS <alias>` to avoid double-aliasing the
  synthetic UNION-ALL subquery), added a
  `CatalogRepository.list_storage_tables` method that
  introspects DuckDB's `information_schema` so DDL-created shards
  surface (`MemoryCatalogRepository` accepts an optional engine
  so ephemeral-mode servers can answer the storage query without
  a `DuckDBCatalogRepository` upgrade), and taught the REST
  schema renderer (`build_response_schema` in
  `src/bqemulator/jobs/executor.py`) to dedupe column names with
  a `_<n>` suffix so self-joins match BigQuery's wire-format
  guarantee. See ADR 0023 §1.C closure note for full details.

### Bucket D — Unqualified routine reference — ✅ Closed

* **Status**: closed in the parity-closure session (all
  4 fixtures moved from XFAIL to PASS).
* **Historical symptom**: `CREATE TEMP FUNCTION foo(...); SELECT
  foo(...)` failed with `Routine reference must have 2 or 3 parts:
  foo`.
* **Historical root cause**: emulator's routine resolver required
  a fully `project.dataset.routine` reference. BigQuery resolves
  single-part identifiers against the script's local scope.
* **Fixtures closed**: `sql_udf_int_to_int`, `sql_udf_string_param`,
  `sql_udf_returns_array`, `sql_udf_returns_struct`.
* **Closure approach**: added a script-local TEMP-function
  registry (`src/bqemulator/udf/temp_registry.py`). Each
  `ScriptInterpreter` instance owns one for the lifetime of one
  script's `run()`. `CREATE TEMP FUNCTION foo(...)` with a
  single-part identifier routes through
  `_exec_create_temp_function`, materialises the routine under a
  registry-unique synthetic dataset (`_bqemu_temp_<uuid-hex>`),
  and remembers the bare name → `RoutineMeta` mapping.
  `_resolve_ref` checks the registry first for single-part refs
  (ADR 0023 §1.D local-scope lookup pass); `_run_query` and its
  parameterised siblings rewrite bare `foo(args)` to the
  qualified flat name before the rest of the SQL pipeline runs;
  `run` drops every materialised TEMP macro in a `finally` arm so
  TEMP functions never leak into the catalog nor across script
  invocations. See ADR 0023 §1.D closure note for full details.

### Bucket E — Multi-statement scripting column naming — ✅ Closed

* **Status**: closed in the parity-closure session
  (1 direct XPASS — `script_if_then`; 1 reclassified to Bucket I —
  `script_exception_handler` — because its expected
  `outcome='caught'` value depends on `SELECT 1 / 0` raising, but
  DuckDB returns `Inf` for a zero divisor so the script's
  EXCEPTION handler never fires).
* **Symptom (historical)**: a multi-statement script's final SELECT
  returned the result with a placeholder column name (`$1`,
  `_col0`) instead of the inferred BigQuery name.
* **Root cause (historical)**: the scripting interpreter's
  `_rewrite_vars_to_params` replaced bare script-variable
  references with bound parameters; BigQuery's "single identifier
  → use as column name" inference was erased and DuckDB returned
  `$1` as the column name.
* **Closure**: scripting interpreter's `_rewrite_vars_to_params`
  in `src/bqemulator/scripting/interpreter.py` now wraps the
  placeholder in an `Alias` when the column is a top-level SELECT
  projection (predicate: `isinstance(col.parent, exp.Select) and
  col.arg_key == "expressions"`). Regression coverage:
  `tests/unit/scripting/test_interpreter.py`
  (`TestProjectionNameInference` — 4 cases). See ADR 0023 §1.E
  closure note for full details.

### Bucket F — Multi-statement DDL extra-row surface — ✅ Closed

* **Status**: closed in the parity-closure session
  (all 3 originally-pinned fixtures moved from XFAIL to PASS:
  `clone_basic`, `mv_basic`, `snapshot_basic`).
* **Symptom (historical)**: `CREATE SNAPSHOT/CLONE/MV...;
  SELECT...` failed under the emulator — DuckDB rejected the
  versioning-DDL syntax with a parser error before the trailing
  SELECT could run. The original symptom statement
  ("extra rows") was a polite simplification of the actual
  outcome.
* **Root cause (historical)**: the scripting interpreter sent
  every statement through the standard translator → DuckDB
  pipeline. Versioning DDL never reached the matching
  `SnapshotTableManager` / `CloneManager` /
  `MaterializedViewManager`. The top-level executor's
  `_maybe_run_versioning_ddl` did fire for multi-statement
  scripts, but the MV regex's lazy `.+?\s*;?\s*$` match
  greedy-crossed statement boundaries. Compounding: setup tables
  created via SQL DDL never updated the catalog cache, so even a
  correct dispatch would have 404-ed at the manager's
  `catalog.get_table(source_table)` precondition.
* **Closure**: three coordinated parts — per-statement
  versioning-DDL dispatch in `ScriptInterpreter._exec_sql`, a
  single-statement gate on `execute_query_job`'s
  `_maybe_run_versioning_ddl` fast path, and a new
  `src/bqemulator/catalog/ddl_sync.py` module that auto-registers
  plain `CREATE [OR REPLACE] TABLE` outputs in the catalog. The
  interpreter's `_exec_sql` also now only updates `_final_table`
  when the executed statement is row-producing
  (`isinstance(tree, exp.Query)`). Regression coverage:
  `tests/unit/scripting/test_interpreter.py`
  (`TestLastStatementWins` — 5 cases) and the new
  `tests/unit/catalog/test_ddl_sync.py` (10 cases). See ADR 0023
  §1.F closure note for full details.

### Bucket G — RANGE / INTERVAL wire format — ✅ Closed

* **Status**: closed in the parity-closure session (all
  20 originally-pinned fixtures moved from XFAIL to PASS, plus 1
  incidental Bucket I closure — `specialized_types/interval_zero`).
* **Historical symptom**: schema or value serialisation diverged
  for RANGE-typed and INTERVAL-typed columns. DuckDB didn't parse
  `RANGE<DATE>` literals (returned `Parser Error: Expected a
  constant as type modifier`); INTERVAL canonical string
  formatting reached the wire under the STRING fallback because
  `_arrow_type_to_bq_type` had no ``pa.types.is_interval`` branch,
  so the BigQuery Python client treated the value as an opaque
  string rather than a `relativedelta`. RANGE columns surfaced as
  RECORD with nested `fields` rather than the
  ``{type: "RANGE", rangeElementType}`` shape with the
  `[start, end)` wire-format cell string.
* **Historical fixtures** (all 20): `range_contains_no`,
  `range_contains_yes`, `range_overlaps_no`, `range_overlaps_yes`,
  `range_intersect_basic`, `range_intersects_empty`,
  `range_date_literal`, `range_datetime_literal`,
  `range_timestamp_literal`, `range_ends_with_inf`,
  `range_starts_with_inf`, `range_array_aggregate`,
  `range_equality_check`, `generate_range_array_date`
  (re-categorised from Bucket A),
  `interval_day_to_second`, `interval_year_to_month`,
  `justify_days_basic`, `justify_hours_basic`,
  `justify_interval_basic`, `make_interval_basic`.
* **Closure**: three coordinated fixes — a pre-translator pass in
  `src/bqemulator/sql/rewriter/specialized_types.py` that rewrites
  ``Cast(literal, RANGE<T>)`` to
  ``STRUCT(CAST(<start> AS T) AS start, CAST(<end> AS T) AS end)``;
  a shared ``detect_range_element`` helper in
  `src/bqemulator/types/range_type.py` that drives the REST schema
  renderer (`_maybe_range_schema_entry`) and the row renderer
  (`_bq_range_metadata`) so RANGE columns surface on the wire as
  `{type: "RANGE", mode, rangeElementType: {type: T}}` with the
  canonical `[start, end)` cell string; and an
  ``is_interval(arrow_type) → "INTERVAL"`` branch in
  `_arrow_type_to_bq_type`. The `GenerateRangeArrayRule` gained
  type-preserving casts and end-clipping via
  ``LEAST(x + step, rng."end")``. Regression coverage:
  `tests/unit/sql/test_specialized_types_rewriter.py`
  (`TestRangeLiteralRewrite` — 7 cases),
  `tests/unit/types/test_range_type.py` (`TestDetectRangeElement`
  — 12 cases), `tests/unit/api/test_arrow_type_to_bq.py`
  (`TestRangeSchemaEntry` — 4 cases + INTERVAL parametrisation),
  `tests/unit/storage/test_arrow_bridge.py`
  (`TestArrowToBqRowsRangeWireFormat` — 7 cases), and updated
  `tests/unit/sql/rules/test_range_rules.py` `TestGenerateRangeArray`
  (DATE-preserving + end-clipping). See ADR 0023 §1.G closure note
  for full details.

### Bucket H — GEOGRAPHY WKT whitespace — ✅ Closed

* **Status**: closed in the parity-closure session.
  Option H.1 (comparison-helper extension) selected over H.2
  (DuckDB-spatial upstream patch). ADR 0022 §3 gained a WKT-shaped
  STRING sub-rule under the STRING tolerance contract; a 6-line
  extension to `tests/conformance/_comparison.py`'s
  `_compare_scalar` wires the sub-rule in. 7 direct XPASSes:
  `st_astext_point`, `st_geogfromtext_point`,
  `st_geogfromtext_linestring`, `st_geogfromtext_polygon`,
  `st_geogfromwkb_point`, `st_geogfromgeojson_point`,
  `st_geogpoint`. 4 fixtures reclassified rather than closed:
  3 small-scale spheroidal entries (`st_centroid_polygon`,
  `st_intersection_polygons`, `st_dwithin_no`) shifted under
  ADR 0019 because the divergence is geometric value drift, not
  stringification; 1 GeoJSON-formatting entry
  (`st_asgeojson_point`) initially shifted to
  `out-of-scope.md#geojson-output-formatting`, then closed the
  same day via scope-expansion #18 (a
  `StAsGeoJsonStringTypeRule` SQL rule + ADR 0022 §3 JSON-shaped
  STRING amendment).
* **Symptom (historical)**: `ST_ASTEXT` and related stringifying
  functions returned WKT with `POINT (1 2)` (extra space) where
  BigQuery emits `POINT(1 2)`. Different from ADR 0019
  spheroidal-vs-planar — this is a *formatting* divergence on the
  string output.
* **Root cause (historical)**: DuckDB-spatial's WKT formatter
  inserts a space between the geometry-type keyword and the
  opening paren; BigQuery's does not. The comparison helper
  normalised WKT for cells declared `GEOGRAPHY`, but `ST_ASTEXT`
  returns `STRING` (not GEOGRAPHY) — so the helper applied
  STRING's exact-equality rule and reported a mismatch.
* **Fix**: a STRING-typed cell whose value matches the anchored
  regex
  ``^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(``
  (case-insensitive) routes through the existing `_normalise_wkt`
  helper before equality comparison. Both sides must match the
  WKT shape to trigger the rule so one-sided drift still
  surfaces as a real mismatch. Regression coverage in a new
  `tests/unit/conformance/test_comparison_wkt_string.py` (20
  unit cases). The slice-2-close count was 23; the 
  Bucket B closure removed 5 (`st_geogfromtext_multipoint`,
  `st_isring_line`, `st_npoints_line`, `st_numpoints_polygon`,
  `st_pointn_line` — narrow-width integer types the widened
  Arrow→BigQuery type-mapper now surfaces as INTEGER); the
  Bucket I closure removed 7 more
  (`st_geometrytype_linestring`, `st_geometrytype_point`,
  `st_geometrytype_polygon`, `st_geometrytype_multipoint`,
  `st_convexhull_points`, `st_envelope_polygon`,
  `st_makepolygon_from_ring` — closed by the new
  `StGeometryTypeBqNameRule`). The Bucket H session handled the
  final 11 (7 direct + 4 reclassified). Net registry delta:
  18 → 11.

### Bucket I — Standard-function semantic differences — ✅ Closed

* **Status**: closed in the parity-closure session
  (36 direct Bucket I XPASSes — the 36 includes `null_date_add`,
  which XPASSed early in the I-a run when the `DATE_ADD`-cast
  pre-translate fired on its NULL-operand shape — plus 7
  incidental Bucket H XPASSes via the new
  `StGeometryTypeBqNameRule`; 2 Bucket I fixtures originally
  pinned to permanent `out-of-scope.md` entries:
  `bound_bignumeric_max` (DuckDB DECIMAL(38) cap; remains pinned)
  and `script_exception_handler` (div/0 raise — reconsidered the
  same day as scope-expansion #17 and closed via the new
  `division_by_zero` pre-translator)). All 38 originally-pinned
  Bucket I entries triaged.
* **Symptom (historical)**: heterogeneous function-level
  divergences in DATE / FORMAT / PARSE / JSON / STRUCT / hash /
  string / boundary / Unicode / GEOGRAPHY type-name spaces.
* **Root causes (historical)**: DuckDB widens DATE arithmetic to
  TIMESTAMP; DuckDB's day-of-week is 0-indexed (Sun=0) vs
  BigQuery's 1-indexed (Sun=1); DuckDB's WEEK is ISO 8601 vs
  BigQuery's Sunday-start Gregorian; SQLGlot collapses TO_JSON and
  TO_JSON_STRING to the same DuckDB SQL; SQLGlot drops the
  `occurrence` argument from 4-arg INSTR; DuckDB's UPPER doesn't
  apply the ß → SS Unicode case-fold; DuckDB's FARM_FINGERPRINT
  Python stand-in used SHA-256; DuckDB's ST_GeometryType returns
  uppercase WKT names where BigQuery returns `ST_<PascalCase>`;
  and float-precision drift in
  `int(ts.timestamp() * 1_000_000)` blew up at the 
  TIMESTAMP boundary.
* **Fix**: four new pre-translator rewriter modules
  (`rewriter/datetime_helpers.py`, `rewriter/json_helpers.py`,
  `rewriter/struct_helpers.py`, `rewriter/safe_helpers.py`) +
  a 4-arg-INSTR extension to the existing
  `rewriter/string_helpers.py` + one new post-translate rule
  module (`rules/datetime_semantics.py`, ten rules:
  `ExtractDateFromTimestampRule`, `ExtractDayofweekRule`,
  `ExtractWeekSundayStartRule`, `ConcatStringTypeRule`,
  `ApproxCountDistinctExactRule`, `ApproxQuantilesDiscreteRule`,
  `FormatPrintfRule`, `ParseTimeRule`, `JsonTypeLowerRule`,
  `ParseTimestampUtcRule`) + a `StGeometryTypeBqNameRule` added
  to `rules/spatial.py` + an `UpperUnicodeRule` added to
  `rules/string_helpers.py` + a generalised
  `DateTruncCalendarUnitRule` (DAY/MONTH/QUARTER/YEAR) in
  `rules/iso_date_parts.py` + three builtin Python UDF changes in
  `builtin_udfs.py` (a bit-exact pure-Python port of FarmHash
  `Fingerprint64` *replaces* the prior SHA-256 stand-in, plus two
  new helpers `bqemu_upper_unicode` and `bqemu_instr_occurrence`)
 + an `arrow_bridge.py` TIMESTAMP renderer switch from
   `int(ts.timestamp() * 1_000_000)` to integer `timedelta`
   arithmetic + an `information_schema.py` rewriter switch of
   `_ts_literal` from `TIMESTAMP_MILLIS(N)` to a typed
   `TIMESTAMP '... UTC'` literal so it sidesteps the new
   datetime-helpers pre-translate. 77 new unit cases under
   `tests/unit/sql/rules/test_datetime_semantics.py`,
   `tests/unit/sql/rewriter/test_datetime_helpers.py`,
   `tests/unit/sql/rewriter/test_bucket_i_helpers.py`,
   `tests/unit/sql/test_builtin_udfs_bucket_i.py`, and
   `tests/unit/sql/rules/test_spatial.py::TestStGeometryTypeBqName`.
   See [ADR 0023 §1.I closure block](../adr/0023-conformance-divergence-baseline.md)
   for the per-sub-session detail.

### Bucket J — Emulator-side missing function translation — ✅ Closed

* **Status**: closed in the parity-closure session
  (41 direct XPASSes + 3 J→I cascades + 2 incidental Bucket I
  closures: `bound_numeric_min`, `json_parse_basic`). All 44
  originally-pinned Bucket J entries triaged.
* **Symptom (historical)**: any query that called a BigQuery
  builtin without a SQLGlot translation rule to a DuckDB
  equivalent failed with `Catalog Error: Scalar Function with
  name X does not exist!`.
* **Root cause (historical)**: the SQLGlot transpiler shipped
  translations for the BigQuery functions exercised in Phases
  1–10; functions outside that path never got a rule.
* **Fix**: four new SQLGlot rule modules (`json_helpers.py`,
  `iso_date_parts.py`, `string_helpers.py`, `misc_helpers.py`)
 + a SAFE-arithmetic expansion of `safe_math.py` + three new
   pre-translator rewriters (`rewriter/string_helpers.py`,
   `rewriter/aggregate_variants.py`,
   `rewriter/numeric_literals.py`) + a new
   `sql/builtin_udfs.py` registering Python-helper UDFs at
   `DuckDBEngine.start` + a `bqemu.duckdb_type` field-metadata
   override on `fetch_arrow` so JSON-typed columns surface on the
   REST wire as `type: "JSON"` instead of the Arrow-derived
   `STRING`. 75 new unit cases under `tests/unit/sql/rules/`
   cover every rule + pre-rewriter path against a live DuckDB
   connection.
* **Cascade**: 3 Bucket J entries cascaded into Bucket I once
  their functions became invocable but the value still diverged
  from real BigQuery — `agg_approx_quantiles` (APPROX_QUANTILE
  algorithm difference), `math_rand_ish_deterministic`
  (FARM_FINGERPRINT bit-pattern mismatch — the Python helper
  uses SHA-256), `bound_bignumeric_max` (39-integer-digit
  BIGNUMERIC literal exceeds DuckDB's DECIMAL(38, …) cap). Two
  unrelated Bucket I entries incidentally XPASSed: `bound_numeric_min`
  and `json_parse_basic`. Net registry delta: 159 → 116.

### ADR 0019 — Spheroidal-vs-planar GEOGRAPHY (8 fixtures, permanent v1.0 divergence)

Continental scale (5 fixtures, pinned since slice-2):

* `st_distance_continental`, `st_area_continental`,
  `st_length_continental`, `st_perimeter_continental`,
  `st_buffer_continental`.

Small-scale reclassified from Bucket H (3 fixtures):

* `st_centroid_polygon` — planar centroid sits at exactly
  `(2, 2)` for the unit-square test; the spheroidal centroid is
  `(2.00000000000004, 2.00040218892024)`.
* `st_intersection_polygons` — planar straight-edge intersection
  vs spheroidal geodesic-edge intersection (bulges by ~1.2e-3
  degrees).
* `st_dwithin_no` — planar Euclidean distance over the
  `(0, 0) ↔ (0, 90)` pair is 90 coordinate units where the
  spheroidal distance is ~10⁷ metres; the 100-metre threshold
  falls on the opposite side of each.

* **Root cause**: BigQuery's GEOGRAPHY is spheroidal (WGS-84
  ellipsoid); DuckDB-spatial is planar (Cartesian). At continental
  scales the numeric results diverge by 0.1–10% depending on
  geometry; at smaller scales the divergence shows up in *derived*
  shape outputs (centroid offset, intersection bulge, distance
  threshold flip).

* **Closure**: would require shipping s2geometry or shapely +
  projection code. Permanently out of scope for v1.0
  (see [`out-of-scope.md#spheroidal-geometry-on-geography`](out-of-scope.md#spheroidal-geometry-on-geography)).

## 2. Locked v1.0 exclusions (`out-of-scope.md`)

These are explicitly *not* implemented and not planned for v1.0.
Each has a documented rationale and (where applicable) a
documented workaround.

| Feature | Reason | Workaround |
|---|---|---|
| **BigQuery ML** (`CREATE MODEL`, `ML.PREDICT`, `ML.EVALUATE`, `ML.FORECAST`, `ML.GENERATE_*`, all model types) | Comparable in size to the rest of the emulator. See [ADR 0012](../adr/0012-bqml-out-of-scope.md). Only Models *resource CRUD* (metadata) is supported. | Run ML training/inference outside the emulator. |
| **BI Engine** | Performance optimisation with no observable semantic effect; irrelevant for a local single-process emulator. | N/A — queries return the same results, just without the in-memory acceleration tier. |
| **Reservations / assignments / capacity commitments** | Billing-plane concepts; emulator has no billing. | N/A. |
| **Slot and byte-billing simulation** | No local analog. | `dryRun` requests still return a best-effort `totalBytesProcessed` estimate from catalog statistics. |
| **Data Transfer Service** | Dozens of separate connector integrations. | Use external schedulers. |
| **Scheduled queries** | Scheduling plane, not SQL semantics. | Use local cron / CI scheduler against the emulator. |
| **Cloud Logging / Cloud Monitoring integration** | Emulator exposes its own Prometheus + OpenTelemetry. | Wire those to your own observability stack. |
| **Cross-region replication** | No geographic model. | N/A. |
| **IAM enforcement** | Emulator is an integration-test target, not an authorisation gateway. Policies are *stored and returned* via REST so client code that round-trips them works; they are *not enforced*. Row-access policies, by contrast, ARE enforced. | Test enforcement against real BigQuery in a separate stage. |
| **Durable Storage Write API stream state** | Streams are in-memory only. Restarting the emulator drops in-flight PENDING/BUFFERED rows. See [ADR 0013](../adr/0013-write-api-strategies.md). | Retry-with-offset on COMMITTED streams within a single process lifetime. |
| **Storage Write API `updated_schema` propagation** | Emulator doesn't yet support ALTER TABLE on active tables across concurrent writers. | Use writer-supplied `writer_schema` as authoritative. |
| **Storage Write API `trace_id` and `missing_value_interpretations`** | Diagnostic-only fields; no row-persistence effect. | N/A. Could be revisited if community asks. |
| **Online backup of a running emulator** | Requires WAL-aware filesystem or a write surface on the admin router. Both are larger than the v1.0 charter. See [ADR 0020](../adr/0020-admin-import-export.md). | Run on a CoW filesystem (btrfs/ZFS/Docker volume snapshot) and snapshot the volume; or stop → backup → restore. |
| **`PersistenceMode.IMPORT` enum value** | Live schema sync would double the credential surface and add an ongoing dependency on the real BigQuery REST API — incompatible with offline test environments. | One-shot `bqemulator import --from-project=…` then `persistence_mode=PERSISTENT`. |
| **`RANGE_SESSIONIZE`** (♻️ reconsidered — scheduled for v1.0 closure) | TVF that rewrites a FROM-clause table reference; the emulator's rewriter pipeline operated on expressions until the Bucket J closure expanded the rewriter machinery. With that machinery in place the closure is bounded. | Express sessionisation with `LAG()` + running sum of session-boundary indicators (full snippet in `out-of-scope.md`). Replaced by direct support when the scope-expansion lands. |
| **BIGNUMERIC literals with > 28 integer digits** (added from Bucket I closure) | DuckDB's widest `DECIMAL` is `DECIMAL(38, s)` — 38 total digits, where BigQuery's BIGNUMERIC holds 38 integer + 38 fractional. Matching the full range would require bundling a wide-decimal library or replacing DuckDB. | Stay within DuckDB's `DECIMAL(38, s)` range. The `standard_functions/bound_bignumeric_max` conformance fixture is the only entry that exercises this corner. |
| **Spheroidal geometry on GEOGRAPHY** | DuckDB-spatial is planar. Spheroidal correctness would require s2geometry or shapely + projection code. | Validate spatial *shape* in CI; validate spatial *correctness* against real BQ in a separate stage. |

## 3. Conformance-coverage gaps (works, but not in the corpus)

The following emulator surfaces work — exercised by unit /
property / integration / E2E tiers — but the conformance corpus
does not yet test them. This is not "the emulator doesn't support
it"; this is "we don't have a recorded-against-real-BQ baseline
for it". Adding these would extend the corpus beyond pure SQL.

| Surface | Where it's tested today | Why not in conformance |
|---|---|---|
| **JS UDFs** (`CREATE TEMP FUNCTION... LANGUAGE js`) | Unit, integration (`tests/integration/test_udf*.py`) | Gated on `[udf-js]` extra (`mini-racer`); deterministic enough to record but no fixtures yet authored. |
| **TVFs** (table-valued functions) | Unit, integration | No fixtures authored yet. |
| **Storage Read API** (gRPC `BigQueryRead`) | Integration, E2E (`test_storage_read_*`) | Non-SQL surface; conformance is the SQL tier. |
| **Storage Write API** (gRPC `BigQueryWrite`, 4 stream types) | Integration, E2E (`test_storage_write_*`) | Non-SQL surface. |
| **Load jobs** (CSV/NDJSON/Parquet → table) | Integration, E2E | Non-SQL surface; not deterministic across CSV/JSON parser variations. |
| **Extract jobs** (table → CSV/JSON/Parquet) | Integration, E2E | Same — non-SQL surface. |
| **Copy jobs** (INSERT INTO target SELECT FROM source) | Integration | Could be added; haven't been. |
| **`tabledata.insertAll`** (streaming inserts) | Integration, E2E | Non-SQL HTTP endpoint. |
| **Row-access-policy enforcement** | Integration (`tests/integration/test_row_access_*`) | RAP creation is `INSERT INTO INFORMATION_SCHEMA` style metadata — doesn't have a clean conformance shape against real BQ without an org-level setup. |
| **Authorized-view delegation** | Integration | Same — metadata-bound. |
| **Time-travel** (`FOR SYSTEM_TIME AS OF...`) | Integration (`tests/integration/test_time_travel.py`) | Deliberately excluded by [ADR 0022 §1.2](../adr/0022-conformance-corpus-design.md) — non-deterministic baselines. |
| **MV refresh** | Integration, chaos | Multi-step + time-dependent. |
| **BEGIN/COMMIT/ROLLBACK transactions** | Integration | Few fixtures authored; multi-statement edge cases. |
| **Session variables (`SET...`)** | Integration | Session state — non-replayable. |
| **`CREATE PROCEDURE`** | Integration | Larger scripting surface; underrepresented in corpus. |
| **Query parameters** (positional `?` / named `@p`) | Integration | Conformance currently submits queries as plain strings; would need parameter-bound fixtures. |
| **`dryRun` cost estimation** | Integration | Best-effort estimate diverges from real BQ by design. |
| **Job lifecycle** (cancel, list, get) | Integration, E2E | Non-SQL HTTP endpoints. |
| **Query result pagination** | Integration, E2E | Non-SQL HTTP endpoint. |
| **Error-message-shape parity** | ✅ **Conformance (via P3.a)** | Conformance now compares BigQuery's exact `reason` / `location` / `http_status` / `message` for fixtures with an `error` envelope. ADR 0022 §3 ``Error parity`` documents the contract; 20 error-shape fixtures recorded against real BigQuery; emulator-side error renderer rewritten (new `jobs/error_mapper.py`) to match BQ's wire format. All 20 fixtures pass with zero divergences pinned. |

## 4. Bottom line

| Question | Answer |
|---|---|
| Does the emulator pass a recorded BigQuery baseline on every fixture we expect it to match? | **Yes — 655 / 655 (100%)** of non-divergent fixtures pass (was 442/442 at slice-2 close; +24 from Bucket A, +8 from Bucket C, +4 from Bucket D, and +1 from Bucket E, all; +3 from Bucket F, +43 from Bucket J, +34 from Bucket B, and +21 from Bucket G; +43 from Bucket I, +7 from Bucket H, +1 from scope-expansion #18 GeoJSON, +1 from scope-expansion #17 strict div/0, +3 from scope-expansion #15 RANGE_SESSIONIZE — three new fixtures recorded — and **+20 from P3.a error-message-shape parity** — all). |
| How many divergences are documented? | **9 fixtures total** — 8 spheroidal-vs-planar from ADR 0019 (5 continental + 3 small-scale reclassified from Bucket H) + 1 Bucket I out-of-scope entry (`bound_bignumeric_max` for DuckDB's `DECIMAL(38)` cap, pinned in `out-of-scope.md`). Each entry is rooted in an ADR-anchored rationale. |
| How many features are *permanently* excluded from v1.0? | **16** locked exclusions in `out-of-scope.md` (was 17 at the Bucket I close; the same-day scope-expansion #15 closure of `RANGE_SESSIONIZE` brought it to 16. The short-lived strict div/0 entry was added by the Bucket I closure and removed the same day by scope-expansion #17; the short-lived GeoJSON output formatting entry was added by the Bucket H closure and removed the same day by scope-expansion #18, so both netted zero). |
| How many divergences have a clear closure path? | **0 of 9** — all 9 remaining divergences are permanent v1.0 entries (8 ADR 0019 spheroidal + 1 BIGNUMERIC out-of-scope). |
| Are there *undocumented* gaps? | The conformance corpus surfaces what it can see — it tests 1018 distinct PASS fixtures (985 SQL + 28 HTTP + 20 gRPC - 15 XFAILs = 1018) plus 15 documented divergences across the 1033-fixture corpus. Untested-in-conformance surfaces (Section 3) work in the emulator and pass other test tiers; they have not been recorded against real BigQuery, so subtle wire-format or value drift in those surfaces would not be caught by conformance today. |

The corpus and this gap analysis are *living documents*: future
slices close buckets, remove entries from `divergences.py`, and
shrink ADR 0023. All ten ADR 0023 buckets (A–J) are now closed;
the residual **15 entries** are a stable mix of permanent design
divergences — 11 ADR 0019 spheroidal, 2 ADR 0024 HLL++, 1 IAM-
fundamental, 1 BIGNUMERIC > 28 digits. No closure-eligible
divergence remains for v1.0.
