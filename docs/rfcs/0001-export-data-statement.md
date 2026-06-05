---
rfc: "0001"
title: "EXPORT DATA statement (Cloud Storage)"
status: Accepted
authors:
  - "@jjviscomi"
created: 2026-06-05
updated: 2026-06-05
supersedes: null
superseded-by: null
---

# RFC 0001: EXPORT DATA statement (Cloud Storage)

> Accepted under the maintainer fast-track described in the
> [RFC lifecycle](README.md): the design was ratified before drafting and
> implementation proceeds in the same PR series. The implementation outcome is
> recorded in [ADR 0043](../adr/0043-export-data-statement.md).

## Summary

Add first-class support for BigQuery's `EXPORT DATA OPTIONS(...) AS
query_statement` GoogleSQL statement, exporting query results to Cloud Storage
URIs (`gs://…`, resolved through the existing `BQEMU_GCS_LOCAL_ROOT` filesystem
shim) in `CSV`, `NEWLINE_DELIMITED_JSON`, `AVRO`, and `PARQUET`. The statement
runs as a `QUERY` job reporting `statementType` `EXPORT_DATA`, reuses the
extract job's DuckDB `COPY` writer, and implements size-based wildcard sharding
that mirrors BigQuery's file-naming scheme. Exporting to external systems
(`EXPORT DATA WITH CONNECTION` → Amazon S3, Azure Blob, Pub/Sub) is out of
scope; the emulator's charter is BigQuery + its Cloud Storage integration.

## Motivation

`EXPORT DATA` is unsupported today, and it fails in the worst possible way —
*silently, then confusingly*. Run through the translator, the statement is
accepted and mangled rather than rejected:

```
IN : EXPORT DATA OPTIONS(uri='gs://b/out/*.csv', format='CSV', overwrite=true) AS SELECT 1 AS a, 'x' AS b
OUT: Ok -> "EXPORT DATA  AS SELECT 1 AS a, 'x' AS b"
```

SQLGlot parses the statement as `exp.Export` with its `OPTIONS` intact in the
AST, but **drops the entire `OPTIONS(...)` clause** when transpiling to DuckDB.
`SQLTranslator.translate()` returns `Ok`, so the destination-less statement
reaches DuckDB — which has no `EXPORT DATA` statement — and dies with a parser
error. The construct slips past the `_UNSUPPORTED_KEYWORDS` quick-reject guard
in `src/bqemulator/sql/translator.py` (which only lists `ML.*`), violating that
file's own stated contract: *"detect early and fail with a clear error instead
of a confusing DuckDB parse failure."*

The gap is also undocumented — `EXPORT DATA` appears in neither
[`out-of-scope.md`](../reference/out-of-scope.md), the gap analysis, nor the
conformance surface inventory.

`EXPORT DATA` is the SQL-native export path that BigQuery users write directly
in queries, scheduled queries, and dbt models — distinct from the `extract`
*job* (`bq extract` / `jobs.insert` with `configuration.extract`), which the
emulator already supports. A user migrating real BigQuery SQL that contains
`EXPORT DATA` currently gets a misleading DuckDB error and no path forward.
Doing nothing leaves a silent parity hole on a common surface.

## Guide-level explanation

`EXPORT DATA` writes the rows of a query to one or more files in Cloud Storage:

```sql
-- CSV with a header and a custom delimiter
EXPORT DATA OPTIONS (
  uri = 'gs://my-bucket/exports/customers_*.csv',
  format = 'CSV',
  header = true,
  field_delimiter = '|',
  overwrite = true
) AS
SELECT id, name FROM my_dataset.customers ORDER BY id;

-- Parquet, Snappy-compressed, single file (no wildcard)
EXPORT DATA OPTIONS (
  uri = 'gs://my-bucket/exports/snapshot.parquet',
  format = 'PARQUET',
  compression = 'SNAPPY'
) AS
SELECT * FROM my_dataset.events;
```

Against the emulator, `gs://` URIs resolve through `BQEMU_GCS_LOCAL_ROOT` exactly
as load/extract jobs do today: `gs://my-bucket/exports/snapshot.parquet` →
`$BQEMU_GCS_LOCAL_ROOT/my-bucket/exports/snapshot.parquet`. A test (or a
[fake-gcs-server](../adr/0034-scio-beam-emulator-routing.md) sidecar sharing the
same root) reads the bytes straight back.

**Single file vs. sharding.** A URI without a wildcard writes one file. A URI
with a single `*` wildcard *shards*: the `*` is replaced by a zero-based,
12-digit, left-padded counter (`…000000000000`, `…000000000001`, …), matching
BigQuery. Output that fits under the per-file size limit produces a single
`…000000000000` shard. An `ORDER BY` in the query is preserved across shards
(rows distributed sequentially), again matching BigQuery.

`EXPORT DATA` also works inside scripts (`BEGIN … END`), because it flows through
the same single-statement execution path as a standalone query job.

## Reference-level explanation

### Syntax

```
EXPORT DATA
  [WITH CONNECTION connection_name]   -- rejected: out of scope (see below)
  OPTIONS (export_option_list)
AS query_statement
```

### OPTIONS

| Option | Type | Applies to | Default | Notes |
|---|---|---|---|---|
| `uri` | STRING | all | — (required) | `gs://…`; zero or one `*` wildcard (see *URI rules*). |
| `format` | STRING | all | `CSV` | `CSV`, `NEWLINE_DELIMITED_JSON` (alias `JSON`), `AVRO`, `PARQUET`. `ORC` rejected (parity — BigQuery does not export ORC; see [`out-of-scope.md`](../reference/out-of-scope.md)). |
| `compression` | STRING | all | `NONE` | CSV/JSON: `GZIP`/`NONE`. AVRO: `DEFLATE`/`SNAPPY`/`NONE` (not `GZIP`). PARQUET: `SNAPPY`/`GZIP`/`ZSTD`/`NONE`. |
| `overwrite` | BOOL | all | `false` | When `false` and any target file exists → error. |
| `header` | BOOL | CSV | `true` | Emit a header row. |
| `field_delimiter` | STRING | CSV | `,` | Column delimiter; `\t`/`tab` accepted for tab. |
| `use_avro_logical_types` | BOOL | AVRO | (see *Unresolved questions*) | Map types to Avro logical types. |

Unknown options and format/option mismatches (e.g. `header` on `PARQUET`) are
rejected with a clear `InvalidQueryError` rather than silently ignored.

### URI rules

- Exactly **zero or one** `*` wildcard; the wildcard may appear anywhere in the
  object name. Two or more wildcards → error.
- The wildcard is replaced by a zero-based counter left-padded to 12 digits.
- A **wildcard-free** URI writes a single file. If the result would exceed the
  per-file size limit, BigQuery errors and requires a wildcard; the emulator
  mirrors this against a **configurable** limit (below).
- Resolution reuses `_resolve_uri` (`gs://bucket/obj` →
  `$BQEMU_GCS_LOCAL_ROOT/bucket/obj`; `file://` and bare paths supported) and
  `_validate_local_path`. Parent directories are created as needed.

### Sharding

Per-shard sizing uses the materialized result's in-memory Arrow size
(`pyarrow.Table.nbytes`) as a proxy for on-disk size:
`shard_count = max(1, ceil(nbytes / threshold))`, rows sliced into contiguous
ranges (preserving `ORDER BY`). The threshold is a new setting
`export_shard_threshold_bytes` (`BQEMU_EXPORT_SHARD_THRESHOLD_BYTES`),
defaulting to ≈1 GiB so realistic small exports produce one file (parity), and
overridable to a small value so tests exercise multi-file sharding
deterministically. The `nbytes` proxy is an explicit emulator-scale
approximation (see *Drawbacks*).

### Execution model

`EXPORT DATA` is a **`QUERY` job** — `JobType` stays
`Literal["QUERY", "LOAD", "EXTRACT", "COPY"]`; no new job type.
`classify_statement_type` / `_classify_parsed_tree` classify `exp.Export` as
`"EXPORT_DATA"`, and two thin entry points share one core: `execute_query_job`
routes a standalone statement to `_execute_export_data_job`, and the scripting
interpreter's `_exec_sql` handles a scripted statement inline. Both call the
shared `parse_export_data` + `write_export`:

1. `parse_export_data` parses the statement, rejects `WITH CONNECTION`, and lifts
   the OPTIONS + inner query out of the `exp.Export` AST (which still carries the
   OPTIONS that SQLGlot drops on a DuckDB transpile).
2. The **inner SELECT** runs through the *normal* single-statement pipeline
   (`_run_query_body` for jobs, the interpreter's `_run_query` for scripts), so
   every BigQuery→DuckDB rule, row-access policy, MV refresh, wildcard-table
   expansion, qualified-name rewrite, and query parameter applies to the exported
   query exactly as to a bare `SELECT`.
3. `write_export` materialises the result once (`ctx.engine.fetch_arrow`), shards
   it, and writes each shard via a writer helper refactored out of
   `execute_extract_job` (the format → DuckDB `COPY (…) TO '…' (FORMAT …)`
   dispatch, including the `avro`-extension error handling). `execute_extract_job`
   adopts the same helper.

The job stores a zero-row result and reports `statementType = EXPORT_DATA`
(via `_build_query_statistics`).

### Result and statistics

The job returns no result rows. `statistics.query.statementType` is
`EXPORT_DATA`, and `statistics.query.exportDataStatistics` carries the
written-file and exported-row counts as int64-strings (`{fileCount, rowCount}`),
alongside the `totalPartitionsProcessed` and `transferredBytes` fields BigQuery
emits for an export job. This shape is **pinned by conformance fixtures recorded
from real BigQuery**: `http_corpus/jobs/export_csv_query_job` for the REST job
resource, and `sql_corpus/export_data/*` for the `statement_type`.

### Error shapes

Clear, BigQuery-shaped errors for: missing/empty `uri`; more than one `*`;
wildcard-free URI whose output exceeds the size limit; unknown or mismatched
OPTIONS; `overwrite = false` with an existing target; `gs://` URI when
`BQEMU_GCS_LOCAL_ROOT` is unset; and `WITH CONNECTION`. `ORC` is rejected the
way BigQuery rejects it — as an invalid `format` OPTIONS value (`invalidQuery`,
HTTP 400, `location = "query"`), not as a distinct unsupported-feature error.
Exact envelopes are pinned by recorded conformance fixtures
(`export_missing_uri`, `export_orc_rejected`).

## Drawbacks

- **Memory.** The result is fully materialized to shard by size; the emulator
  does not stream. Acceptable at emulator data scale, but unlike BigQuery's
  streaming export.
- **Sharding is approximate.** `nbytes` (in-memory) is not the compressed
  on-disk size, so the emulator's shard boundaries can differ from BigQuery's at
  the margin. We document this rather than pretend byte-exact parity.
- **Multi-shard parity isn't conformance-recorded.** Triggering BigQuery's
  >1 GB sharding during recording is impractical, so conformance records the
  single-file (`…000000000000`) case; multi-shard behavior is guaranteed by
  construction and covered by unit/property tests with a low threshold.
- **Scope creep risk.** Sharing a writer with `execute_extract_job` couples two
  surfaces; mitigated by keeping the extract job's externally observable
  behavior unchanged.

## Rationale and alternatives

- **Intercept pre-translation in the shared single-SQL path** (chosen) vs. a
  translator keyword-reject (loses OPTIONS, can't execute) vs. a new REST
  job-type (`EXPORT DATA` is a SQL statement / query job, and a job-type would
  miss the scripted case). The chosen point preserves OPTIONS from the AST and
  covers both standalone and scripted statements with one code path.
- **Reuse the extract `COPY` writer** (chosen) vs. a parallel writer — reuse
  removes a duplicated format dispatch (the repo enforces duplication limits)
  and keeps export/extract byte-output consistent.
- **Real size-based sharding** (chosen) vs. always-single-shard vs. lenient
  literal-path — chosen for faithful file-count and naming parity, accepting the
  `nbytes` approximation and the in-memory cost.
- **GCS only** (chosen) vs. external sinks — matches the emulator's charter;
  external connections are a much larger surface real BigQuery treats as
  separate services.

## Prior art

- [ADR 0027](../adr/0027-load-extract-avro-orc.md) — the load/extract Avro/ORC
  work; its `COPY`-based extract writer and `avro`-extension handling are what
  this RFC reuses, and its ORC-export exclusion sets the format-parity precedent.
- [ADR 0020](../adr/0020-admin-import-export.md) — the admin backup/restore path
  uses DuckDB's unrelated `EXPORT DATABASE`; this RFC's `EXPORT DATA` is a
  distinct GoogleSQL statement (the name collision is noted to avoid confusion).
- [ADR 0015](../adr/0015-scripting-execution-model.md) — the scripting
  statement-dispatch model that routes scripted `EXPORT DATA` through the shared
  path.
- Real BigQuery:
  [Export statements](https://cloud.google.com/bigquery/docs/reference/standard-sql/export-statements)
  and [Exporting data to Cloud Storage](https://cloud.google.com/bigquery/docs/exporting-data).

## Unresolved questions

- The exact `statistics.query` field set and the precise `statementType` string
  for `EXPORT_DATA` are resolved by the conformance recording: `statementType =
  "EXPORT_DATA"`, `exportDataStatistics {fileCount, rowCount}`, and the sibling
  `totalPartitionsProcessed` / `transferredBytes` fields. The error envelopes
  are likewise pinned — `invalidQuery` / HTTP 400 for an invalid `format` value
  (including `ORC`) and for a missing/empty `uri`.
- `use_avro_logical_types` default and whether DuckDB's `COPY … (FORMAT AVRO)`
  honors it; the precise DuckDB `COPY` option mapping for each `compression`
  value per format (notably PARQUET `ZSTD`/`GZIP` and AVRO `DEFLATE`/`SNAPPY`).
- `field_delimiter` alias handling (`\t` / `tab`) and multi-byte delimiters.

## Future possibilities

- Route the `extract` *job*'s REST `csvOptions`/compression and result
  statistics through the same shared writer (it currently honors neither).
- `EXPORT DATA WITH CONNECTION` to external sinks (S3 / Blob / Pub/Sub).
- Streaming / byte-exact sharding if a use case demands large-export fidelity.
