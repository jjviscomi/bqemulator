# Emulator-vs-BigQuery gap analysis

Comprehensive catalogue of every known difference between bqemulator
and real Google BigQuery. Sources, in priority order:

1. **[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)** —
   **15 fixtures** pinned to `xfail(strict=True)`. Every entry is
   rooted in either [ADR 0019](../adr/0019-specialized-types.md)
   (spheroidal-vs-planar) or [out-of-scope.md](out-of-scope.md).
   All entries are permanent design decisions with no v1.0 closure
   plan.
2. **[`out-of-scope.md`](out-of-scope.md)** — **19 features**
   explicitly excluded from v1.0.0 with rationale.
3. **Untested-in-conformance surfaces** — capabilities the emulator
   ships but the conformance corpus doesn't yet exercise (because
   they aren't SQL, are non-deterministic, or are exercised in
   adjacent tiers).

A "gap" in this document means *anything* a user might rely on in
real BigQuery that the emulator does not currently provide
identically. The catalogue is intentionally exhaustive — most
entries are permanent v1.0 exclusions.

## At a glance

| Source | Count | What it is |
|---|---|---|
| **ADR 0019 spheroidal GEOGRAPHY** | **11 fixtures** | Permanent v1.0 divergence (S2-sphere semantics for the helper rules vs planar / no-spherical fallback for the rest). 3 buffer fixtures + 8 pre-existing surfaces (4 `st_asgeojson_*` interpolation, `st_centroid_polygon`, `st_intersection_polygons`, `st_buffer_continental`, `st_asbinary_point`). |
| **ADR 0024 HLL++ sketch binary format** | **2 fixtures** | `agg_hll_count_init_basic` + `agg_hll_count_merge_partial_basic` — BigQuery's HLL++ sketch BYTES format is undocumented. The 2 sibling EXTRACT/MERGE surfaces pass cleanly via `COUNT(DISTINCT)`. |
| **`out-of-scope.md` locked exclusions (fixture-bearing)** | **2 fixtures** | `bound_bignumeric_max` (DuckDB DECIMAL(38) cap on 39-integer-digit literals; `out-of-scope.md#bignumeric-literals-with-39-integer-digits`) + `row_access/caller_information_schema_visibility` (IAM-fundamental; `out-of-scope.md#iam-enforcement`). |
| **`out-of-scope.md` locked exclusions (no-fixture)** | **17 features** | Surfaces explicitly excluded from v1.0 with rationale but with no corpus fixture (BQML, BI Engine, billing, scheduling, multi-region, etc.). |
| **Conformance coverage gaps** | **~12 surfaces** | Shipped emulator features not yet exercised by the conformance corpus (covered in other test tiers). |

Total **15** documented runtime divergences — 11 ADR 0019
spheroidal + 2 ADR 0024 HLL++ + 2 out-of-scope.md fixture-bearing
entries — plus **19** total locked exclusions in `out-of-scope.md`
(17 no-fixture + 2 fixture-bearing) + ~12 untested-in-conformance
surfaces.

## 1. Runtime behavioural divergences

Each divergence has full root-cause analysis in
[ADR 0023](../adr/0023-conformance-divergence-baseline.md);
per-fixture rationale strings live in `divergences.py`.

### ADR 0019 — Spheroidal-vs-planar GEOGRAPHY (11 fixtures, permanent v1.0 divergence)

Continental scale (5 fixtures):

* `st_distance_continental`, `st_area_continental`,
  `st_length_continental`, `st_perimeter_continental`,
  `st_buffer_continental`.

Small-scale geometric drift (3 fixtures):

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

GeoJSON / binary edge cases (3 fixtures):

* `st_asgeojson_*` interpolation — BigQuery interpolates midpoints
  along geodesic arcs on long edges where DuckDB-spatial does not.
* `st_asbinary_point` — BigQuery encodes `ST_GEOGPOINT(1, 1)` via
  an ECEF→lng/lat round-trip that loses 1 ULP per axis.

* **Root cause**: BigQuery's GEOGRAPHY is spheroidal (S2 sphere
  with constant earth radius `kEarthRadiusMeters = 6371010.0`);
  DuckDB-spatial is planar (Cartesian). At continental scales
  the numeric results diverge by 0.1–10% depending on geometry;
  at smaller scales the divergence shows up in *derived* shape
  outputs (centroid offset, intersection bulge, distance
  threshold flip).

* **Closure**: would require shipping s2geometry or shapely +
  projection code. Permanently out of scope for v1.0
  (see [`out-of-scope.md#spheroidal-geometry-on-geography`](out-of-scope.md#spheroidal-geometry-on-geography)).

### ADR 0024 — HLL++ sketch binary format (2 fixtures, permanent v1.0 divergence)

* `standard_functions/agg_hll_count_init_basic`
* `standard_functions/agg_hll_count_merge_partial_basic`

* **Root cause**: BigQuery's `HLL_COUNT.INIT` /
  `HLL_COUNT.MERGE_PARTIAL` return a BYTES sketch in a specific
  HyperLogLog++ binary format documented only in the HLL++ paper —
  not in a wire-format specification.

* **Closure**: bit-exact reproduction would require test-driven
  reverse-engineering of BigQuery's bucket-count selection,
  Murmur3 hash variant, sparse/dense representation switch,
  header framing, and bias-correction tables. The user-facing
  cardinality semantic *is* preserved — the two sibling
  EXTRACT/MERGE patterns pass via `COUNT(DISTINCT)`. See
  [ADR 0024](../adr/0024-hll-count-support-strategy.md) and
  [`out-of-scope.md`](out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial).

### `out-of-scope.md` fixture-bearing entries (2 fixtures)

* `standard_functions/bound_bignumeric_max` — BigQuery's BIGNUMERIC
  max value (`5.7896…e38`) has 39 integer digits; DuckDB's widest
  DECIMAL is `DECIMAL(38, s)` — 38 total digits. Matching the
  full BIGNUMERIC range would require bundling a wide-decimal
  library or replacing DuckDB. See
  [`out-of-scope.md#bignumeric-literals-with-39-integer-digits`](out-of-scope.md#bignumeric-literals-with-39-integer-digits).
* `row_access/caller_information_schema_visibility` — BigQuery's
  `INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` requires
  `bigquery.rowAccessPolicies.list` IAM permission; the emulator
  does not enforce IAM. See
  [`out-of-scope.md#iam-enforcement`](out-of-scope.md#iam-enforcement).

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
| **BIGNUMERIC literals with ≥ 39 integer digits** | DuckDB's widest `DECIMAL` is `DECIMAL(38, s)` — 38 total digits, where BigQuery's BIGNUMERIC holds 38 integer + 38 fractional. Matching the full range would require bundling a wide-decimal library or replacing DuckDB. | Stay within DuckDB's `DECIMAL(38, s)` range. The `standard_functions/bound_bignumeric_max` conformance fixture is the only entry that exercises this corner. |
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
| **`dryRun` cost estimation** | Integration | Best-effort estimate diverges from real BQ by design. |
| **Job lifecycle** (cancel, list, get) | Integration, E2E | Non-SQL HTTP endpoints. |
| **Query result pagination** | Integration, E2E | Non-SQL HTTP endpoint. |

## 4. Bottom line

| Question | Answer |
|---|---|
| Does the emulator pass a recorded BigQuery baseline on every fixture we expect it to match? | **Yes** — 100% of non-divergent conformance fixtures pass. |
| How many divergences are documented? | **15 fixtures total** — 11 ADR 0019 spheroidal + 2 ADR 0024 HLL++ + 2 `out-of-scope.md` fixture-bearing entries. Each is rooted in an ADR-anchored rationale. |
| How many features are *permanently* excluded from v1.0? | **19 locked exclusions** in `out-of-scope.md` (17 no-fixture + 2 fixture-bearing). |
| How many divergences have a clear closure path? | **0** — every remaining divergence is a permanent v1.0 entry. |
| Are there *undocumented* gaps? | The conformance corpus surfaces what it can see — it tests 1141 SQL + 48 HTTP + 26 gRPC fixtures plus 15 documented divergences. Untested-in-conformance surfaces (Section 3) work in the emulator and pass other test tiers; they have not been recorded against real BigQuery, so subtle wire-format or value drift in those surfaces would not be caught by conformance today. |

The corpus and this gap analysis are *living documents*. The
residual **15 entries** are a stable mix of permanent design
divergences — 11 ADR 0019 spheroidal, 2 ADR 0024 HLL++, 1 IAM-
fundamental, 1 BIGNUMERIC ≥ 39 digits. No closure-eligible
divergence remains for v1.0.
