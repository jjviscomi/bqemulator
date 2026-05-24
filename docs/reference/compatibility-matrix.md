# Compatibility matrix

Auto-regenerated each release from test results.

Legend: ✅ supported · ⚠️ partial · ❌ unsupported / out of scope

## REST API

| Resource | Operation | Status |
|---|---|---|
| Projects | list | ✅ |
| Projects | getServiceAccount | ✅ |
| Datasets | list / get / insert / patch / update / delete | ✅ |
| Datasets | undelete | ⚠️ no-op stub |
| Tables | list / get / insert / patch / update / delete | ✅ |
| Tables | getIamPolicy / setIamPolicy | ⚠️ no-op stub |
| Jobs | query (synchronous) | ✅ |
| Jobs | insert (query type) / get / getQueryResults | ✅ |
| Jobs | insert (load / extract / copy) / list / cancel / delete | ✅ |
| Jobs | Load from local file (multipart upload) | ✅ |
| Jobs | Load from local file (resumable upload) | ✅ |
| TableData | insertAll / list | ✅ |
| Routines | CRUD | ✅ |
| Models | CRUD (metadata only) | ✅ |
| Row access policies | CRUD + enforcement | ✅ |

## gRPC Storage Read API

| Operation | Format | Status |
|---|---|---|
| CreateReadSession, ReadRows | Arrow IPC | ✅ |
| CreateReadSession, ReadRows | Avro | ✅ |
| SplitReadStream | — (format-agnostic) | ✅ |

The canonical
`BigQueryReadClient.create().createReadSession(...)` Java code path
(Avro is Java's default wire format) runs unchanged against the
emulator. See [ADR 0030](../adr/0030-storage-read-avro-format.md).

## gRPC Storage Write API

| Stream type | Input format | Status |
|---|---|---|
| DEFAULT | Arrow / proto | ✅ |
| COMMITTED | Arrow / proto | ✅ |
| PENDING | Arrow / proto | ✅ |
| BUFFERED | Arrow / proto | ✅ |

## SQL features

| Feature | Status |
|---|---|
| SELECT / JOIN / WHERE / GROUP BY / HAVING / ORDER BY / LIMIT | ✅ |
| CTEs (WITH, WITH RECURSIVE) | ✅ |
| Window functions | ✅ |
| ARRAY, STRUCT, UNNEST | ✅ |
| MERGE / INSERT / UPDATE / DELETE / TRUNCATE | ✅ |
| Transactions (BEGIN / COMMIT / ROLLBACK) | ✅ |
| Partitioning (time-unit / ingestion / integer-range) | ✅ |
| Clustering | ✅ |
| Wildcard tables (`_TABLE_SUFFIX`) | ✅ |
| SQL UDFs | ✅ |
| JavaScript UDFs | ✅ |
| Table-valued functions | ✅ |
| Procedural scripting (DECLARE / BEGIN / END / IF / LOOP) | ✅ |
| Time travel (`FOR SYSTEM_TIME AS OF`) | ✅ |
| Table snapshots + CLONE | ✅ |
| Materialized views | ✅ |
| Row access policies (enforced) | ✅ |
| Authorized views | ✅ |
| INFORMATION_SCHEMA (SCHEMATA, TABLES, COLUMNS, TABLE_OPTIONS, VIEWS, PARTITIONS, ROUTINES, MATERIALIZED_VIEWS, ROW_ACCESS_POLICIES) | ✅ — JOBS family deliberately [out of scope](out-of-scope.md#information_schemajobs-family) |
| GEOGRAPHY (via spatial extension) | ✅ |
| RANGE<T> | ✅ |
| INTERVAL | ✅ |
| BigQuery ML | ❌ [out of scope](out-of-scope.md#bigquery-ml) |

## Types

| Type | Status |
|---|---|
| INT64, FLOAT64, BOOL, STRING, BYTES | ✅ |
| NUMERIC, BIGNUMERIC | ✅ |
| DATE, TIME, DATETIME, TIMESTAMP | ✅ |
| JSON | ✅ |
| ARRAY<T>, STRUCT<…> | ✅ |
| GEOGRAPHY | ✅ |
| RANGE<T> | ✅ |
| INTERVAL | ✅ |

## Load + extract file formats

| Format | Load | Extract |
|---|---|---|
| CSV | ✅ | ✅ |
| NEWLINE_DELIMITED_JSON | ✅ | ✅ |
| PARQUET | ✅ | ✅ |
| AVRO | ✅ | ✅ |
| ORC | ✅ (requires `[orc]` extra) | ❌ ([out of scope](out-of-scope.md#orc-extract) — BigQuery itself does not extract to ORC) |

bqemulator is a **strict superset of both goccy and BigQuery** on
the load/extract format axis: equal to BigQuery on the
intersect-with-BigQuery set, ahead of goccy on Parquet extract +
Avro/ORC load + Avro extract. See
[ADR 0027](../adr/0027-load-extract-avro-orc.md) for the design
decisions behind the Avro extension + `pyorc` integration.

## Supported clients

bqemulator's REST + gRPC surfaces are exercised by **five
conformance clients** on every CI run. Every shipped feature is
proven against this matrix; a regression that surfaces in one
client but not another fails the build.

| Client | Surface |
|---|---|
| `google-cloud-bigquery` (Python) | REST + gRPC |
| `@google-cloud/bigquery` (Node.js) | REST + gRPC |
| `cloud.google.com/go/bigquery` (Go) | REST + gRPC |
| `com.google.cloud:google-cloud-bigquery` (Java) | REST + gRPC |
| `bq` CLI (gcloud SDK) | REST |

The bq CLI does not exercise the Storage Read or Storage Write gRPC
surfaces — it has no commands that talk to those APIs. See
[ADR 0032](../adr/0032-bq-cli-conformance-client.md) for design
notes and [`docs/guides/using-bq-cli.md`](../guides/using-bq-cli.md)
for the canonical configuration recipe.

goccy `bigquery-emulator`'s
[FEATURE.md](https://github.com/goccy/bigquery-emulator/blob/main/docs/feature-support.md)
documents Python, Ruby, PHP, Node.js, Java, and the `bq` CLI as
its supported-clients matrix. bqemulator is a strict superset on
this axis at v1.0 tag time (we ship Go in place of Ruby/PHP; the
latter two are deferred to a follow-up RFC if user demand
surfaces).

<!-- BEGIN AUTO-GENERATED CONFORMANCE SNAPSHOT -->

## Conformance corpus snapshot

> **Auto-generated.** Edit fixtures under [`tests/conformance/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance) or update the XFAIL registry in [`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py), then run `make compat-matrix` to regenerate this block. The CI gate (`--check`) refuses to merge a PR whose committed snapshot has drifted from the corpus.

- **Corpus totals**: 1233 fixtures (1159 SQL + 48 HTTP + 26 gRPC); **1220 PASS / 13 XFAIL**
- **XFAIL contract**: every pin in `KNOWN_DIVERGENCES` references an ADR or `out-of-scope.md` section — invented divergences are forbidden (see [ADR 0023](https://github.com/jjviscomi/bqemulator/blob/main/docs/adr/0023-conformance-divergence-baseline.md)).

### Per-phase fixture coverage

Each row aggregates fixtures by corpus (SQL / HTTP / gRPC) and the on-disk phase directory. The `Status` column is derived: `✅` when every fixture in the phase passes; `⚠` when at least one fixture is pinned in the XFAIL registry.

| Corpus | Phase | Fixtures | PASS | XFAIL | Status |
|---|---|---:|---:|---:|:---:|
| SQL | `api_configuration` | 63 | 63 | 0 | ✅ |
| SQL | `information_schema` | 18 | 18 | 0 | ✅ |
| SQL | `partitioning_clustering` | 23 | 23 | 0 | ✅ |
| SQL | `rest_crud` | 149 | 149 | 0 | ✅ |
| SQL | `routines_scripting` | 58 | 58 | 0 | ✅ |
| SQL | `row_access` | 23 | 22 | 1 | ⚠ |
| SQL | `specialized_types` | 150 | 143 | 7 | ⚠ |
| SQL | `standard_functions` | 651 | 646 | 5 | ⚠ |
| SQL | `versioning` | 24 | 24 | 0 | ✅ |
| HTTP | `jobs` | 48 | 48 | 0 | ✅ |
| gRPC | `storage_read` | 16 | 16 | 0 | ✅ |
| gRPC | `storage_write` | 10 | 10 | 0 | ✅ |
| **Total** | | **1233** | **1220** | **13** | ⚠ |

### XFAIL pin registry

All 13 entries in [`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py) — each rationale references an ADR or [`out-of-scope.md`](https://github.com/jjviscomi/bqemulator/blob/main/docs/reference/out-of-scope.md) section so closure paths stay traceable.

| Fixture id | Rationale (short) |
|---|---|
| [`row_access/caller_information_schema_visibility`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/row_access/caller_information_schema_visibility) | P2.d: INFORMATION_SCHEMA.ROW_ACCESS_POLICIES requires bigquery.rowAccessPolicies.list IAM permission. |
| [`specialized_types/spheroidal_buffer_neighborhood_match`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/spheroidal_buffer_neighborhood_match) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/spheroidal_buffer_state_xfail`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/spheroidal_buffer_state_xfail) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/spheroidal_buffer_street_match`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/spheroidal_buffer_street_match) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/st_asbinary_point`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/st_asbinary_point) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/st_buffer_continental`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/st_buffer_continental) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/st_centroid_polygon`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/st_centroid_polygon) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`specialized_types/st_intersection_polygons`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/specialized_types/st_intersection_polygons) | Spheroidal-vs-planar divergence — see ADR 0019 and docs/reference/out-of-scope.md#spheroidal-geometry-on-geog… |
| [`standard_functions/agg_hll_count_init_basic`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/agg_hll_count_init_basic) | HLL sketch BYTES format differs — see docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--… |
| [`standard_functions/agg_hll_count_merge_partial_basic`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/agg_hll_count_merge_partial_basic) | HLL sketch BYTES format differs — see docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--… |
| [`standard_functions/bound_bignumeric_max`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/bound_bignumeric_max) | BIGNUMERIC literal with 39 integer digits exceeds DuckDB's DECIMAL(38, 0) cap; literals with ≤ 38 integer dig… |
| [`standard_functions/dt_format_date_min`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/dt_format_date_min) | FORMAT_DATE %Y year-padding differs from BigQuery for years < 1000: BQ emits '1-01-01' for DATE '0001-01-01',… |
| [`standard_functions/tpcds_q47`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/standard_functions/tpcds_q47) | TPC-DS Q47-style multi-CTE pattern: a CTE that carries a window aggregate (AVG OVER PARTITION BY ... |

<!-- END AUTO-GENERATED CONFORMANCE SNAPSHOT -->
