# Out of scope

This project follows a **no-deferral principle**: when a feature is in
scope, it ships complete in its phase. Scope boundaries are explicit —
recorded here with rationale — and never presented as "coming in v1.1".
Anything on this page can be reconsidered for v2 as a separate product
decision, typically via an [RFC](../rfcs/README.md).

Each excluded feature raises a clear `UnsupportedFeatureError` when
encountered, with a link back to this page.

## Excluded in v1.0.0

### BigQuery ML

`CREATE MODEL`, `ML.PREDICT`, `ML.EVALUATE`, `ML.FORECAST`, `ML.GENERATE_*`,
and all ML-related model types (ARIMA, k-means, matrix factorization, DNN,
boosted trees, AutoML).

Only **Models resource CRUD** — list / get / insert / patch / update / delete
of model metadata — is supported.

*Rationale*: full BQML would be a project of comparable size to the rest
of the emulator. See [ADR 0012](../adr/0012-bqml-out-of-scope.md).

### BI Engine

BigQuery's in-memory acceleration tier.

*Rationale*: a performance optimization with no observable semantic effect.
Irrelevant for a local emulator that runs in a single process.

### Reservations, assignments, capacity commitments

BigQuery's slot billing model.

*Rationale*: billing-plane concepts; emulator has no billing.

### Slot and byte-billing simulation

Query cost accounting.

*Rationale*: no local analog and no cost model. `dryRun` requests
parse + validate but `statistics.query.totalBytesProcessed` is
always returned as `"0"` (the emulator has no way to estimate
bytes scanned — DuckDB's row-storage layout doesn't map onto
BigQuery's columnar pricing). Treat `totalBytesProcessed` as
"validation passed", not as a cost estimate.

### Data Transfer Service

Scheduled transfers from external sources (Google Ads, Campaign Manager,
S3, etc.).

*Rationale*: dozens of connectors, each a separate integration. Different
product scope.

### Scheduled queries

Cron-managed query runs in the `bigquery-data-transfer.googleapis.com` surface.

*Rationale*: scheduling plane, not SQL semantics. Use a local cron / CI
scheduler to run queries against the emulator.

### Cloud Logging / Cloud Monitoring integration

Export of emulator activity to Google-hosted observability.

*Rationale*: emulator exposes its own Prometheus and OpenTelemetry
instrumentation; integration with Google's logging stack is not useful
locally.

### Cross-region replication

Dual-region or multi-region dataset replication semantics.

*Rationale*: no geographic model in the emulator.

### IAM enforcement

IAM policies on datasets and tables are **stored and returned** from the
REST API (so client code that round-trips them works), but they are
**not enforced** — the emulator accepts any credentials.

Row access policies, by contrast, ARE enforced — queries are rewritten
to apply the policy's filter.

*Rationale*: the emulator is an integration-test target, not an
authorization gateway. Enforcing IAM would require a real identity
provider.

*Conformance fixtures pinned to this section*:
- ``row_access/caller_information_schema_visibility``
  (real BigQuery returns ``404 NotFound`` on
  ``INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`` queries when the caller
  lacks ``bigquery.rowAccessPolicies.list``; the emulator surfaces
  the policy row because IAM is not enforced)

### Durable Storage Write API stream state

`BigQueryWrite` streams are kept **in memory only** — a process restart
drops every in-progress stream, including any buffered rows in `PENDING`
and `BUFFERED` streams that were never flushed. Clients that restart
mid-write must use retry-with-offset on `COMMITTED` streams (the
emulator correctly returns `ALREADY_EXISTS` for already-ingested pages
within a single process lifetime).

*Rationale*: the emulator is ephemeral-by-default (`EPHEMERAL`
persistence mode is the recommended CI configuration). Adding durable
stream state would require a persistent WAL layer that duplicates
DuckDB's storage engine without matching BigQuery's production
semantics precisely. See [ADR 0013](../adr/0013-write-api-strategies.md).

### Durable upload session state

Resumable upload sessions opened via the upload host
(`/upload/bigquery/v2/projects/{p}/jobs?uploadType=resumable`) are
kept **in memory only**. A process restart drops every in-progress
session, including the partially uploaded bytes staged on disk under
`Settings.upload_staging_dir`. Clients that restart mid-upload must
restart the upload from offset 0.

*Rationale*: same ephemeral-by-default charter as the Storage Write
API. The session map's lifetime is bound to the emulator's process
lifetime; adding cross-restart persistence would require a side
journal (session id → staging path + received-bytes counter) that
duplicates state already held in the staging file's size on disk
without matching BigQuery's production semantics precisely. The
emulator default of `upload_session_ttl_seconds=3600` is operator-
tunable (1 minute to 24 hours); see
[ADR 0029](../adr/0029-upload-host-endpoints.md).

### Storage Write API schema evolution

Real BigQuery's `AppendRowsResponse.updated_schema` propagates a
table-schema change back to writers when the table is altered
mid-stream. The emulator does not emit this field; writers are expected
to treat the schema supplied in `writer_schema` as authoritative for
the duration of the connection.

*Rationale*: the emulator does not yet support ALTER TABLE on active
tables across concurrent writers.

### Storage Write API trace propagation

`AppendRowsRequest.trace_id` and
`AppendRowsRequest.missing_value_interpretations` are ignored. Values
supplied by the client are not stored or returned.

*Rationale*: diagnostic-only fields in BigQuery; they have no effect on
row persistence. Revisit if the community asks for trace-id pass-through
into the emulator's OpenTelemetry spans.

### Online backup of a running emulator

`bqemulator backup` and `bqemulator restore` require the emulator to
be **stopped**. A running emulator holds an exclusive
DuckDB file lock; both commands open the file directly via
`duckdb.connect` and would deadlock against a live server. Real
BigQuery's implicit always-online backup has no local analog.

*Rationale*: a hot-backup endpoint would add a write surface on the
diagnostic admin router (we kept it read-only on purpose; see ADR
0020) or require WAL-aware filesystem integration. Both are larger in
scope than the integration-test charter v1.0.0 sets.

*Workaround*: run the emulator under a copy-on-write filesystem
(btrfs / ZFS / Docker volume snapshot) and snapshot the underlying
volume while the emulator runs. The snapshot can be restored into a
fresh `data_dir` and started with `bqemulator start --data-dir <snap>`.

### `PersistenceMode.IMPORT` enum value

The enum value `bqemulator.config.PersistenceMode.IMPORT` exists but
no code path reads it. ADR 0020 retired the original "live schema sync
against a real BigQuery project" design in favour of the one-shot
`bqemulator import --from-project=…` CLI command.

*Rationale*: a live-sync persistence mode would double the credential
surface (the server would need ADC) and create an ongoing dependency
on the real BigQuery REST API that's incompatible with offline test
environments — exactly the use case the emulator exists to serve.

*Workaround*: run `bqemulator import` once to materialise schemas,
then start the server normally (`persistence_mode=PERSISTENT`).

The enum value is kept to preserve backwards compatibility for any
caller that hard-coded it. A future v2 deprecation cycle may remove
it; until then, it has no behavioural effect.

### BIGNUMERIC literals with 39 integer digits

BigQuery's `BIGNUMERIC` type holds 38-digit integer-part precision plus
38-digit fractional-part precision (i.e. up to 77 total digits). DuckDB's
widest `DECIMAL` is `DECIMAL(38, s)` — total *digit* count capped at 38.

**Contract**:

* **Literals where ``integer_digits ≤ 38``**: ✅ Accepted. The
  pre-translator's Path C (see
  [`bqemulator.sql.rewriter.numeric_literals`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/numeric_literals.py))
  truncates the fractional part to ``38 - integer_digits`` digits when
  the combined integer + fractional count exceeds 38. Examples:
 * `BIGNUMERIC '1.234567890123456789012345678901234567890'` (1 int +
   39 frac) → stored as `DECIMAL(38, 37)` with the last fractional
   digit dropped.
 * `BIGNUMERIC '12345678901234567890123456789012345678.123456789'`
   (38 int + 9 frac) → stored as `DECIMAL(38, 0)` with the entire
   fractional dropped. Wire-format schema renderer surfaces this
   column as NUMERIC (scale ≤ 9) rather than BIGNUMERIC — see the
   "documented corner case" note below.
* **Literals where ``integer_digits ≥ 39``**: ❌ Rejected. The
  literal falls through to `bqemu_to_bignumeric` which raises an
  `InvalidOperation` / `Conversion Error`. The canonical example is
  BigQuery's BIGNUMERIC max value
  (`5.7896…e38` with 39 integer digits + 38 fractional digits) —
  pinned by `standard_functions/bound_bignumeric_max`.

**Documented corner case**: when Path C truncation drops the
fractional scale to ≤ 9, the schema renderer's "scale > 9 →
BIGNUMERIC" inference falls back to NUMERIC. A bare
`SELECT BIGNUMERIC '12345678901234567890123456789012345678.123'`
(38 int + 3 frac, total 41) lands on the wire as NUMERIC. This only
affects naked `SELECT BIGNUMERIC '…' AS col` queries — BIGNUMERIC
literals bound into BIGNUMERIC-typed columns retain their column
type because the schema is determined by the column definition,
not the literal's scale.

*Rationale (XFAIL'd fixture only)*: matching BigQuery's full
BIGNUMERIC range for the > 38 integer-digit case requires either
bundling a wide-decimal library (e.g. Python's
`decimal.Decimal` is unlimited, but routing it through DuckDB's
storage means storing BIGNUMERIC columns as VARCHAR and rewriting
every arithmetic / comparison / aggregation through a Python
helper UDF — multi-week scope) or replacing DuckDB as the storage
engine. Both are scope expansions far beyond what the conformance
corpus's single fixture warrants.

*Workaround*: for the ``bound_bignumeric_max`` case specifically,
stay within DuckDB's ``DECIMAL(38, 0)`` integer range — 38
integer digits (≈ 1e38) is more than sufficient for every
practical financial / scientific / cryptographic use case the
emulator targets. The fixture stays XFAILed against this section.

*Conformance fixtures pinned to this section*:
- ``standard_functions/bound_bignumeric_max`` (39 integer digits —
  XFAILed against DuckDB's 38-digit DECIMAL cap).

### Spheroidal geometry on GEOGRAPHY

BigQuery's `GEOGRAPHY` uses a **spherical** geometry model — it
uses S2's documented ``kEarthRadiusMeters = 6371010.0``. The
emulator ships **5 translator rules** in
[`bqemulator.sql.rules.spatial`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/spatial.py)
(`StDistanceSpheroidalRule` / `StLengthSpheroidalRule` /
`StAreaSpheroidalRule` / `StPerimeterSpheroidalRule` /
`StDWithinSpheroidalRule`) plus 4 Python helper UDFs
(``bqemu_st_{distance,length,area,perimeter}_spheroidal``) that route
the metric-returning SQL surfaces through 3D-unit-vector great-circle
math (atan2 / cross / dot) and L'Huilier-fan spherical excess on the
S2 sphere. All 4 continental fixtures (``st_distance_continental`` /
``st_area_continental`` / ``st_length_continental`` /
``st_perimeter_continental``), all 12 metric spheroidal fixtures
(6 distance + 1 high-latitude + 3 area + 2 length), and the
small-scale ``st_dwithin_no`` predicate match BigQuery's recording
to within ``rel_tol=1e-12``. The remaining spheroidal-bucket
fixtures below describe surfaces the spherical helpers **do NOT
yet cover**:

* **ST_BUFFER** (4 fixtures: 3 buffer + ``st_buffer_continental``)
  — generating BigQuery's exact 33-vertex geodesic-circle polygon
  needs a per-vertex bearing generator that emits the same azimuth /
  step / radius coordinates as BigQuery's internal algorithm.
* **ST_AsBinary** (1 fixture: ``st_asbinary_point``) — BigQuery
  encodes ``ST_GEOGPOINT(1, 1)`` via an ECEF→lng/lat round-trip that
  loses 1 ULP per axis. Matching the recorded base64 needs the same
  round-trip.
* **ST_AsGeoJSON on multi-vertex shapes** (4 fixtures) — BigQuery
  interpolates midpoints along geodesic arcs in GeoJSON output for
  LINESTRING / MultiLineString / GeometryCollection / MultiPolygon.
* **ST_Centroid + ST_Intersection on small polygons** (2 fixtures) —
  the centroid sits at ``(2, 2.00040218892024)`` spheroidally vs
  exactly ``(2, 2)`` planar; the intersection's edges bulge by
  ~1.2e-3 degrees along geodesics.

*Rationale*: a complete spheroidal implementation (closing every
remaining fixture above) would require the S2 library or equivalent
spheroidal backend — substantial complexity for fixtures that the
existing helpers already close for the common ST_DISTANCE / ST_AREA /
ST_LENGTH / ST_PERIMETER / ST_DWITHIN paths.

**Shape returns — divergence is small but non-zero at every scale.**
``ST_CENTROID``, ``ST_INTERSECTION``, ``ST_ASGEOJSON`` on long edges,
and ``ST_DWITHIN`` (the predicate flips when the planar distance
happens to straddle a meter-scaled threshold the spheroidal distance
does not) all return planar-vs-spheroidal coordinate drift because
geodesics curve while planar lines stay straight. The drift is small
in absolute terms (typically <0.001 degrees at small scales) but
exceeds the ``rel_tol=1e-12`` ``FLOAT64`` tolerance the runner uses
for non-WKT-shaped float comparisons. The small-scale
``st_centroid_polygon``, ``st_intersection_polygons``, and
``st_dwithin_no`` fixtures are examples.

*Rationale*: the emulator is an integration-test target. A correct
spheroidal implementation would require shipping a second geometry
library (s2geometry or shapely + projection code) and bridging it
into DuckDB storage — substantial complexity for a use case where
real BigQuery is the canonical answer. A unit-conversion shim
(``× 111320 × cos(lat)``) for the metric surfaces would still
disagree with the recorded baselines because the underlying geometry
is planar (a 10-km line near the equator measures slightly differently
than the same line at 60°N spheroidally; the cosine-scaling shim
would erase that latitude dependence).

*Workaround*: validate spatial-query *shape* in CI against the
emulator; validate spatial *correctness* (numeric metric values, exact
geodesic-interpolation coordinates) in a separate conformance-against-
real-BQ stage. For development-time sanity checks, the emulator's
relative ordering of distances and the topology of intersections /
buffers is preserved — only the absolute numeric values diverge.

*Conformance fixtures pinned to this section* (the metric fixtures
except buffer, every continental metric, and ``st_dwithin_no`` all
PASS):
- ``specialized_types/st_buffer_continental``
  (BigQuery's 33-vertex geodesic-circle polygon's exact vertex
  coordinates need a per-vertex bearing/step generator the helpers
  don't yet ship)
- ``specialized_types/st_centroid_polygon``
  (the centroid of the unit-degree square is exactly ``(2, 2)``
  planar but ``(2.00000000000004, 2.00040218892024)`` spheroidal —
  needs a spheroidal centroid algorithm beyond the metric helpers)
- ``specialized_types/st_intersection_polygons``
  (the planar intersection follows straight edges where the
  spheroidal one bulges along geodesics — needs a geodesic-arc
  intersection)
- ``specialized_types/st_asbinary_point``
  (BigQuery encodes ``ST_GEOGPOINT(1, 1)`` via an ECEF→lng/lat
  round-trip that loses 1 ULP per axis; recorded
  ``x = 0x3FEFFFFFFFFFFFFE`` ≈ 0.9999999999999998 instead of an
  exact 1.0 — needs ECEF round-trip emulation)
- ``specialized_types/spheroidal_buffer_street_match``
  (10 m radius buffer; same vertex-exactness gap as
  ``st_buffer_continental``)
- ``specialized_types/spheroidal_buffer_neighborhood_match``
  (100 m radius buffer; same root cause)
- ``specialized_types/spheroidal_buffer_state_xfail``
  (100 km radius buffer; same root cause)

### HLL sketch binary format (HLL_COUNT.INIT / MERGE_PARTIAL)

BigQuery's `HLL_COUNT.INIT` and `HLL_COUNT.MERGE_PARTIAL` return a
BYTES sketch in a specific HyperLogLog++ binary format documented in
[the HLL++ paper](https://research.google/pubs/pub40671/) but not in
a wire-format specification. Bit-exact reproduction would require
test-driven reverse-engineering of BigQuery's bucket-count selection,
Murmur3 hash variant, sparse/dense representation switch, header
framing, and bias-correction tables — a multi-week workstream
disproportionate to the user-facing benefit (sketches authored in
BigQuery cannot be persisted to a table the emulator can read, and
vice-versa).

The cardinality user-facing semantic *is* preserved. The emulator
routes the two cardinality-extracting patterns —
`HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))` and `HLL_COUNT.MERGE(sketch)`
over a subquery union of `HLL_COUNT.INIT(x)` legs — to
`COUNT(DISTINCT x)` via [`HllCountExtractInitRule`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/aggregate_types.py)
and [`HllCountMergeRule`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/aggregate_types.py),
following the precedent set by `APPROX_COUNT_DISTINCT` (ADR 0023 §1.I)
and documented in [ADR 0024](../adr/0024-hll-count-support-strategy.md).
For small-cardinality inputs `COUNT(DISTINCT)` and HLL agree exactly;
for inputs above HLL's bucket count the values agree within
~1.04/√m (HLL's documented standard error).

The *sketch-as-persistable-BYTES* semantic is not preserved.
`HLL_COUNT.INIT` and `HLL_COUNT.MERGE_PARTIAL` reach DuckDB unchanged
(both functions have no DuckDB primitive); DuckDB raises a
``CatalogException`` which the emulator surfaces as ``InvalidQueryError``.

*Workaround*: rewrite the query as
`HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))` or `COUNT(DISTINCT x)` when
the sketch output is not required downstream. If sketch persistence
is required, run the query against real BigQuery — the emulator is
not a drop-in replacement for that pattern.

*Conformance fixtures pinned to this section*:
- `standard_functions/agg_hll_count_init_basic`
- `standard_functions/agg_hll_count_merge_partial_basic`

### DBSCAN clustering (ST_CLUSTERDBSCAN)

BigQuery's `ST_CLUSTERDBSCAN(geog, epsilon, min_pts) OVER (window)`
is a window-shaped aggregate that runs the DBSCAN density-based
clustering algorithm over the windowed geometries and assigns each
input a cluster id (or `NULL` for noise points). DuckDB-spatial has
no DBSCAN primitive; a correct emulator-side implementation would
need to:

1. Materialise the windowed geometries.
2. Build an `epsilon`-neighbourhood index over them (a k-d tree or
   ball-tree).
3. Run the DBSCAN cluster-expansion walk with the documented
   `min_pts` minimum density rule.
4. Surface the cluster ids back through the window's row order.

The correctness contract is non-trivial (the spheroidal epsilon
neighbourhood differs from the planar one for continental-scale
inputs; the cluster-expansion order is implementation-defined for
ties; the noise-point assignment branches on density at every
candidate). Combined with the cardinality-quadratic worst-case
runtime over millions of points, the value-to-emulator ratio is
poor for v1.0 — DBSCAN is rarely used in queries the emulator is
otherwise the right substitute for.

We defer the surface to a future release that ships a dedicated
spatial-clustering backend. Until then, `ST_CLUSTERDBSCAN` reaches
DuckDB unchanged and raises `CatalogException` →
`InvalidQueryError`. No conformance fixture is recorded — the
inventory entry stays 🔴 Uncovered in the matrix and the gap
denominator counts the function against the open gap.

*Workaround*: run the clustering off-database (Python +
scikit-learn or PostGIS) and write the cluster ids back to a
BigQuery table the emulator can read.

### Legacy SQL (`useLegacySql=true`)

BigQuery accepts two SQL dialects on the same wire surface:
**Standard SQL** (the default) and **Legacy SQL** (the original
2011-era dialect retained for backward compatibility). The dialect
is selected per-job by the `useLegacySql` boolean on
`QueryJobConfiguration`. Legacy SQL has its own parser, function
catalogue, identifier-quoting rules, scoping rules, NULL handling,
and JOIN syntax — it overlaps with standard SQL only superficially.
A query like `SELECT INTEGER(1)` is valid legacy SQL and invalid
standard SQL; `SELECT CAST(1 AS INT64)` is the reverse.

Supporting legacy SQL inside the emulator would require either:

1. A second translator pipeline (BigQuery legacy → DuckDB) parallel
   to the existing standard-SQL one, with its own SQLGlot dialect,
   its own function-mapping table, its own identifier-resolution
   rules, and its own divergence catalogue. The maintenance burden
   approximately doubles the translator surface.
2. A pre-translator that rewrites legacy SQL to standard SQL before
   the existing pipeline sees it. This is a documented BigQuery-side
   migration path (`bq query --use_legacy_sql=false` after rewriting)
   but it does not cover the constructs that have no standard-SQL
   equivalent (e.g., the `[project:dataset.table]` table-reference
   syntax, the implicit-FLATTEN scoping for repeated fields, the
   table-wildcard `TABLE_DATE_RANGE` family).

BigQuery has officially recommended standard SQL since 2017 and
flagged legacy SQL as legacy in every release-note from 2017
onwards. New projects do not enable it; existing projects with
legacy SQL workloads have an established migration path off it.
The user-impact-to-emulator-effort ratio is poor for v1.0 — clients
that rely on legacy SQL are migrating off it independently of
whether the emulator supports it.

The emulator ships a **narrow legacy-to-standard rewriter** in
[`bqemulator.sql.rewriter.legacy_sql`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/legacy_sql.py)
that handles the type-cast subset (``INTEGER``, ``FLOAT``, ``STRING``,
``BOOLEAN``, ``BYTES``) and the ``[project:dataset.table]`` reference
shape. These rewrites are strict syntactic substitutions —
``INTEGER(x)`` → ``CAST(x AS INT64)``, etc. — so simple legacy
queries that only use these constructs round-trip cleanly through
the standard pipeline.

Queries that use legacy-SQL features outside this subset (JOIN EACH,
WITHIN, FLATTEN, the implicit-correlated-subquery rules, the
``date_add(NOW(), -7, 'DAY')`` form, the `TABLE_DATE_RANGE` family,
etc.) still surface the appropriate translation error from the
standard pipeline. A full legacy-SQL parser remains out of scope.

*Workaround for un-rewritten constructs*: rewrite the query to
standard SQL (the canonical migration path BigQuery itself
recommends) and submit it with `useLegacySql=false` (the default).

### CTE self-join with window aggregate (TPC-DS Q47)

TPC-DS Q47 uses a multi-CTE pattern where a CTE (`v1`) is
defined with two window aggregates — `AVG(SUM(...)) OVER (PARTITION
BY...)` for monthly-average sales plus `RANK() OVER (PARTITION
BY... ORDER BY d_year, d_moy)` for a chronological row-number —
and then **self-joined three times** in a subsequent CTE (`v2`):

```sql
WITH v1 AS (
  SELECT ...,
    AVG(SUM(ss_sales_price)) OVER (PARTITION BY ...) AS avg_monthly_sales,
    RANK() OVER (PARTITION BY ... ORDER BY d_year, d_moy) AS rn
  FROM item, store_sales, date_dim, store
  WHERE ...
  GROUP BY ...
),
v2 AS (
  SELECT v1.*, v1_lag.sum_sales AS psum, v1_lead.sum_sales AS nsum
  FROM v1, v1 v1_lag, v1 v1_lead
  WHERE v1.rn = v1_lag.rn + 1
    AND v1.rn = v1_lead.rn - 1
)
```

When SQLGlot translates this to DuckDB it inlines `v1` three
times into `v2`. DuckDB's planner raises `Binder Error: UNNEST
requires a single list as input` on the resulting plan — the
exact internal step that mis-fires is not yet diagnosed.

Closing this divergence cleanly requires either:

1. Investigating SQLGlot's inlining strategy for CTEs whose
   bodies carry window aggregates and emitting an alternative
   plan (materialise the CTE first via `CREATE TEMP TABLE AS
   SELECT FROM v1` before the self-join). DuckDB does honour
   `CREATE TEMP TABLE`, so a pre-translator that materialises
   any multi-times-referenced CTE with a window aggregate would
   work — but the criteria for "materialise vs inline" need a
   cost model.
2. A DuckDB upstream fix to the planner's UNNEST-related binder
   for the specific shape SQLGlot emits — out of scope here.

The conformance fixture
[`standard_functions/tpcds_q47`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/tpcds_q47)
is pinned XFAIL against this divergence. Q47 is the only one of
the 29 TPC-DS fixtures in the corpus that surfaces this issue; the
other 28 picks PASS without code changes.

*Workaround*: clients that need the same shape should
materialise the CTE manually (`CREATE TEMP TABLE v1_materialised
AS SELECT... FROM v1`) before the self-join, or refactor to
use `LAG`/`LEAD` window functions on the original CTE without
self-joining.

### ORC extract

**Status**: Excluded permanently.

BigQuery itself does **not** support ORC as a destination extract
format. The
[documented set](https://cloud.google.com/bigquery/docs/exporting-data#export_formats_and_compression_types)
is `CSV`, `JSON`, `PARQUET`, `AVRO` only. Shipping ORC extract in
the emulator would put bqemulator *ahead* of BigQuery on a surface
where parity matters — a user who extracts to ORC against the
emulator and then tries to repeat the same job against the real
service would get a surprising failure.

**Workaround**: Extract to Parquet via the existing executor branch,
then convert downstream with `pyorc` or `pyarrow`:

```python
# extract to Parquet from bqemulator, then convert to ORC locally
import pyarrow.parquet as pq
import pyorc

arrow_table = pq.read_table("extract.parquet")
with open("extract.orc", "wb") as fh:
    writer = pyorc.Writer(fh, str(arrow_table.schema))
    writer.write_rows(arrow_table.to_pylist())
    writer.close()
```

See [ADR 0027](../adr/0027-load-extract-avro-orc.md) for the
load/extract format-coverage contract. ORC **load** is supported
via the optional `[orc]` extra; only ORC write is excluded.

The GoogleSQL [`EXPORT DATA`](../guides/exporting-data.md) statement
shares this destination format set: `format = 'ORC'` is rejected as an
invalid `format` OPTIONS value (matching BigQuery), not modelled as a
separate unsupported feature.

### `EXPORT DATA WITH CONNECTION` (external sinks)

**Status**: Excluded permanently.

The GoogleSQL `EXPORT DATA OPTIONS(...) AS SELECT` statement is
[supported](../guides/exporting-data.md) for Cloud Storage destinations
(`gs://`, resolved under `BQEMU_GCS_LOCAL_ROOT`). Its `WITH CONNECTION`
variant — which exports to external systems (Amazon S3, Azure Blob
Storage, Pub/Sub reverse-ETL) through a `CONNECTION` resource — is
**not** supported, and is rejected with a clear
`UnsupportedFeatureError`.

*Rationale*: the emulator's charter is BigQuery and its Cloud Storage
integration. External sinks are separate services that real BigQuery
reaches through connection resources — each a distinct integration well
outside the SQL-semantics surface the emulator targets. See
[ADR 0043](../adr/0043-export-data-statement.md) and
[RFC 0001](../rfcs/0001-export-data-statement.md).

**Workaround**: export to Cloud Storage and move the files to the
external sink with the provider's own tooling.

### INFORMATION_SCHEMA.JOBS* family

**Status**: Excluded permanently.

BigQuery exposes a `JOBS` / `JOBS_BY_PROJECT` / `JOBS_BY_FOLDER` /
`JOBS_BY_ORGANIZATION` family of `INFORMATION_SCHEMA` views that
surface job history (`creation_time`, `total_bytes_processed`,
`total_slot_ms`, `cache_hit`, `user_email`, statement type, etc.).

Job history in the emulator is in-memory only and bounded by the
process lifetime. The `INFORMATION_SCHEMA.JOBS*` views are
typically used for billing- and quota-analysis queries — slot
consumption, bytes billed per user, week-over-week query cost —
neither of which the emulator models. Implementing a partial view
that returned the in-memory job list would give false confidence
to production billing queries that the emulator silently won't
match.

*Rationale*: querying job history is a billing/quota
observability concern, not a SQL-semantics concern. The emulator
has no billing model and no quota subsystem; the views would
return real-looking numbers (rows + bytes from the in-memory
job log) that don't translate to BigQuery's billing model.

**Workaround**: query the REST `jobs.list` endpoint (which IS
shipped — see [api-coverage.md](api-coverage.md)) for the
equivalent metadata. The REST response carries
`statistics.query.totalBytesProcessed`,
`statistics.query.statementType`, `status.errorResult`, and the
other job fields a script-side audit needs:

```python
from google.cloud import bigquery
client = bigquery.Client(project="...", client_options=...)
for job in client.list_jobs(state_filter="DONE", max_results=50):
    print(job.job_id, job.statement_type, job.total_bytes_processed)
```

See [conformance-coverage-matrix.md](conformance-coverage-matrix.md)
for the INFORMATION_SCHEMA coverage inventory. Goccy's
`bigquery-emulator` also defers this surface; the emulator's
parity-with-goccy stance keeps it out of scope.

### Google Cloud Storage emulation

**Status**: Excluded permanently — the emulator's charter is BigQuery,
not GCS.

bqemulator implements the BigQuery REST + gRPC surface. Real BigQuery
treats Google Cloud Storage as an external service; the emulator
follows the same separation. Implementing a GCS HTTP/JSON-API surface
inside the emulator would expand scope from "BigQuery" to "BigQuery +
GCS" — a substantially larger maintenance surface for a feature real
BigQuery doesn't include.

The emulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim
([ADR 0027](../adr/0027-load-extract-avro-orc.md)) is a *filesystem
resolver* for ``gs://`` URIs that appear in LOAD / EXTRACT
``sourceUris`` and ``EXPORT DATA`` destination URIs — it maps
``gs://bucket/path`` to a local filesystem path, so a test can
pre-stage files for a load or read an exported file back. It is
not a GCS API emulator. Anything that needs the actual GCS JSON API
(Beam's ``BigQueryIO.Write`` BATCH_LOADS staging step, the Java SDK's
``Storage.objects.insert``, signed URLs, multipart uploads) must
target a separate GCS emulator.

**Workaround** for Beam BigQueryIO BATCH_LOADS specifically:
[fsouza/fake-gcs-server](https://github.com/fsouza/fake-gcs-server)
ships a Docker image that implements the GCS HTTP/JSON API and stores
objects at ``{root}/{bucket}/{object}`` — byte-identical with
``BQEMU_GCS_LOCAL_ROOT``'s expected layout. The scio example
([`docs/examples/java/scio/`](../examples/java/scio/README.md)) brings
both containers up with a shared bind mount: Beam stages BATCH_LOADS
shards via fake-gcs-server (which materialises them on disk),
bqemulator's LOAD job reads the same bytes via its filesystem
resolver. See [ADR 0034](../adr/0034-scio-beam-emulator-routing.md)
for the full design.

**Reconsidering**: an in-process GCS emulation surface would need an
[RFC](../rfcs/README.md) demonstrating use cases the sidecar pattern
does not cover. The sidecar adds one container to a test fixture; the
in-process alternative would add an entire HTTP/JSON API + multipart
upload + signed URL surface to bqemulator. The cost/benefit currently
favours the sidecar.

### Native Windows containers

The published image (`ghcr.io/jjviscomi/bqemulator`) is a multi-arch
Linux image (`linux/amd64,linux/arm64`). A separate Windows-container
variant (`mcr.microsoft.com/windows/nanoserver` or `servercore` base,
with a `windows/amd64`-tagged manifest entry) is **not** shipped.

*Rationale*: Windows-native containers and Linux containers share no
filesystem layers, so supporting both is a parallel build pipeline
with its own CI matrix, runner cost, and dependency-verification
burden. Several of the emulator's native dependencies have historically
been less reliable on Windows containers — the V8 embedding via
`mini-racer` (UDF runtime), `grpc.aio` (which uses `ProactorEventLoop`
on Windows with documented edge cases against the selector loop the
rest of the codebase assumes), and Storage Read API Avro
materialization in particular. Validating all of these on every
release more than doubles the e2e matrix runtime, against a small
marginal audience — in 2026, ~all Windows backend-Python workflows
run via WSL2 + Docker Desktop with the existing Linux image and
require no changes from us.

**Workaround for Windows users**: install [Docker Desktop for
Windows](https://www.docker.com/products/docker-desktop/) with the
WSL2 backend (the default since Docker Desktop 4.x). The published
Linux image then runs natively under WSL2 — including all networking,
volume mounts, and the published `bqemulator` CLI. No
Windows-specific configuration is required by the emulator itself.

**Reconsidering**: open an [RFC](../rfcs/README.md) documenting a
real-world WSL2-forbidden use case (e.g. a corporate policy that
forbids enabling the Linux subsystem on developer laptops). A native
Windows variant is a candidate for v2 if the gap is widely felt.

## Reconsidering

Every exclusion above has been considered during design. To re-open:

1. Open an [RFC](../rfcs/README.md) describing the use case and proposed
   implementation.
2. The TSC decides by consensus or, failing that, majority vote.
3. On acceptance, an ADR supersedes the relevant section here.
