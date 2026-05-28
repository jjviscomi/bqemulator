# Emulator-vs-BigQuery gap analysis

A navigational overview of every known difference between bqemulator
and real Google BigQuery, organised by category. The **live counts
and per-fixture lists live in their authoritative sources** — linked
throughout — so this page never drifts out of sync with them:

1. **Runtime divergences** — fixtures pinned to `xfail(strict=True)`
   in [`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py).
   The current pinned set and its rationale strings render in the
   [compatibility matrix](compatibility-matrix.md) XFAIL pin registry;
   root-cause analysis lives in
   [ADR 0023](../adr/0023-conformance-divergence-baseline.md). Every
   entry is rooted in an ADR or [out-of-scope.md](out-of-scope.md).
2. **Locked v1.0 exclusions** — features explicitly excluded from
   v1.0.0, each with rationale and (where applicable) a workaround, in
   [out-of-scope.md](out-of-scope.md).
3. **Untested-in-conformance surfaces** — capabilities the emulator
   ships but the conformance corpus doesn't exercise (because they
   aren't SQL, are non-deterministic, or are covered in adjacent test
   tiers). Catalogued in §3 below — this page is the source for that
   list.

A "gap" means *anything* a user might rely on in real BigQuery that
the emulator does not currently provide identically. Most entries are
permanent v1.0 exclusions; none has a v1.0 closure plan.

## 1. Runtime behavioural divergences

Every divergence is a fixture pinned to `xfail(strict=True)` in
[`divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)
with a rationale rooted in an ADR or
[out-of-scope.md](out-of-scope.md). For the **live list** — which
fixtures are pinned right now, with each rationale — see the XFAIL
pin registry in the
[compatibility matrix](compatibility-matrix.md); full root-cause
analysis is in
[ADR 0023](../adr/0023-conformance-divergence-baseline.md). The pins
fall into the following categories.

### Spheroidal-vs-planar GEOGRAPHY (ADR 0019)

BigQuery's `GEOGRAPHY` is **spheroidal** — it uses S2's documented
`kEarthRadiusMeters = 6371010.0`; DuckDB-spatial is **planar**
(Cartesian). The emulator ships spherical-Earth helper rules
(`StDistanceSpheroidalRule` / `StLengthSpheroidalRule` /
`StAreaSpheroidalRule` / `StPerimeterSpheroidalRule` /
`StDWithinSpheroidalRule`) that close the metric-returning surfaces,
so the residual pins are the surfaces those helpers do **not** yet
cover: `ST_BUFFER` geodesic-circle vertex-exactness, `ST_ASBINARY`'s
ECEF→lng/lat round-trip, and the small-polygon `ST_CENTROID` /
`ST_INTERSECTION` geodesic drift. Closing them would require shipping
s2geometry or shapely + projection code. See
[out-of-scope.md#spheroidal-geometry-on-geography](out-of-scope.md#spheroidal-geometry-on-geography).

### HLL++ sketch binary format (ADR 0024)

`HLL_COUNT.INIT` and `HLL_COUNT.MERGE_PARTIAL` return a BYTES sketch
in a HyperLogLog++ binary format documented only in the HLL++ paper,
not in a wire-format specification. The cardinality user-facing
semantic *is* preserved — the `EXTRACT`-of-`INIT` and `MERGE`-over-
subquery patterns route through `COUNT(DISTINCT)`. The
sketch-as-persistable-BYTES semantic is not. See
[ADR 0024](../adr/0024-hll-count-support-strategy.md) and
[out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial](out-of-scope.md#hll-sketch-binary-format-hll_countinit-merge_partial).

### Locked-exclusion divergences (out-of-scope.md)

A few fixtures are pinned against permanent design decisions
documented in [out-of-scope.md](out-of-scope.md), including:

* **BIGNUMERIC literals with ≥ 39 integer digits** — DuckDB's widest
  `DECIMAL` is `DECIMAL(38, s)`. See
  [out-of-scope.md#bignumeric-literals-with-39-integer-digits](out-of-scope.md#bignumeric-literals-with-39-integer-digits).
* **`INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` visibility** — real
  BigQuery gates the view on `bigquery.rowAccessPolicies.list`; the
  emulator does not enforce IAM. See
  [out-of-scope.md#iam-enforcement](out-of-scope.md#iam-enforcement).
* **CTE self-join with window aggregate (TPC-DS Q47)** — a SQLGlot
  CTE-inlining shape DuckDB's binder rejects. See
  [out-of-scope.md#cte-self-join-with-window-aggregate-tpc-ds-q47](out-of-scope.md#cte-self-join-with-window-aggregate-tpc-ds-q47).

Each pin's rationale string in `divergences.py` links to its
out-of-scope.md section.

## 2. Locked v1.0 exclusions

Features explicitly *not* implemented and not planned for v1.0 — each
with a documented rationale and (where applicable) a workaround — are
catalogued in **[out-of-scope.md](out-of-scope.md)**, which is the
single source of truth for this category. This page does not duplicate
those entries.

The excluded surfaces span BigQuery ML, BI Engine,
reservations / billing simulation, the Data Transfer Service,
scheduled queries, Cloud Logging / Monitoring integration,
cross-region replication, IAM enforcement, durable Storage Write API
and upload-session state, online backup of a running emulator, legacy
SQL (beyond the narrow type-cast rewriter), ORC *extract*, the
`INFORMATION_SCHEMA.JOBS*` family, Google Cloud Storage API emulation,
and native Windows containers. See
[out-of-scope.md](out-of-scope.md) for the rationale and workaround on
each.

## 3. Conformance-coverage gaps (works, but not in the corpus)

The following emulator surfaces work — exercised by unit /
property / integration / E2E tiers — but the conformance corpus
does not yet test them. This is not "the emulator doesn't support
it"; this is "we don't have a recorded-against-real-BQ baseline
for it". Adding these would extend the corpus beyond pure SQL. This
table is maintained here (it has no authoritative generator).

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
| **`dryRun` validation** | Integration | `statistics.query.totalBytesProcessed` always returns `"0"` (a "validation passed" marker, not a cost estimate — the emulator has no byte-billing model; see [out-of-scope.md#slot-and-byte-billing-simulation](out-of-scope.md#slot-and-byte-billing-simulation)). |
| **Job lifecycle** (cancel, list, get) | Integration, E2E | Non-SQL HTTP endpoints. |
| **Query result pagination** | Integration, E2E | Non-SQL HTTP endpoint. |

## 4. Bottom line

| Question | Answer |
|---|---|
| Does the emulator pass a recorded BigQuery baseline on every fixture we expect it to match? | **Yes** — 100% of non-divergent conformance fixtures pass. |
| How many divergences are documented? | See the live count in the [compatibility matrix](compatibility-matrix.md) XFAIL pin registry. They fall into three categories: ADR 0019 spheroidal GEOGRAPHY, ADR 0024 HLL++ sketch format, and locked-exclusion fixtures (`out-of-scope.md`). Each is rooted in an ADR-anchored rationale. |
| How many features are *permanently* excluded from v1.0? | See [out-of-scope.md](out-of-scope.md) — the authoritative catalogue of locked exclusions. |
| How many divergences have a clear closure path? | **0** — every remaining divergence is a permanent v1.0 design decision. |
| Are there *undocumented* gaps? | The conformance corpus surfaces what it can see — the live fixture and XFAIL totals are in the [compatibility matrix](compatibility-matrix.md). Untested-in-conformance surfaces (§3) work in the emulator and pass other test tiers; they have not been recorded against real BigQuery, so subtle wire-format or value drift in those surfaces would not be caught by conformance today. |

The corpus and this gap analysis are *living documents*. The residual
divergences are a stable set of permanent design decisions — spheroidal
GEOGRAPHY, HLL++ sketch format, and a handful of locked-exclusion
fixtures — with no closure-eligible divergence remaining for v1.0.
