# ADR 0043: EXPORT DATA statement (Cloud Storage)

- **Status**: Accepted

## Context

[RFC 0001](../rfcs/0001-export-data-statement.md) proposes first-class support
for the GoogleSQL `EXPORT DATA OPTIONS(...) AS query_statement` statement. This
ADR records the implementation decisions that realize it.

Before this work the statement was not merely unsupported — it failed silently
then confusingly. SQLGlot parses `EXPORT DATA` as `exp.Export` with its
`OPTIONS` preserved in the AST, but discards the `OPTIONS(...)` clause when
transpiling to DuckDB; `SQLTranslator.translate()` returned `Ok` for the
destination-stripped statement, which then died inside DuckDB (no such
statement). It slipped past the `_UNSUPPORTED_KEYWORDS` quick-reject in
`src/bqemulator/sql/translator.py` and was absent from the conformance surface
inventory and [`out-of-scope.md`](../reference/out-of-scope.md).

`EXPORT DATA` is the SQL-native sibling of the already-supported `extract` job
([ADR 0027](0027-load-extract-avro-orc.md)). The constraints to satisfy mirror
that work:

1. **Reuse, not duplication.** The extract job already writes `CSV` / `JSON` /
   `PARQUET` / `AVRO` to `gs://` URIs via DuckDB `COPY … TO`. A second writer
   would duplicate the format dispatch (the duplication gate forbids it) and
   risk export/extract output drift.
2. **Coverage.** Every new branch meets the ≥90% line+branch gate; the new
   module targets complete branch coverage and joins the mutation tier
   ([ADR 0026](0026-mutation-tier-design-contract.md)).
3. **Conformance shape.** Recorded against real BigQuery via the HTTP/SQL corpus
   framework ([ADR 0022](0022-conformance-corpus-design.md)); hand-authored
   baselines are forbidden.
4. **Five-language E2E.** Python / Node.js / Go / Java SDKs + the `bq` CLI all
   exercise the statement against a live container (AGENTS.md non-negotiable).

## Decisions

### 1. Detect `exp.Export` at classification time; run the inner SELECT through the standard pipeline

`EXPORT DATA` is a **`QUERY` job** — `JobType` stays
`Literal["QUERY", "LOAD", "EXTRACT", "COPY"]`. `classify_statement_type` /
`_classify_parsed_tree` (`src/bqemulator/jobs/executor.py`) gain an `exp.Export`
→ `"EXPORT_DATA"` branch, and two thin entry points share one core:

- **Standalone job:** `execute_query_job` sees `statement_type == "EXPORT_DATA"`
  and delegates to `_execute_export_data_job`.
- **Scripted statement:** the scripting interpreter's `_exec_sql`
  ([ADR 0015](0015-scripting-execution-model.md)) detects and handles it inline.

Both call the shared `parse_export_data` (which lifts the OPTIONS + inner query
out of the `exp.Export` AST) and `write_export`. Crucially, the **inner SELECT
is run through the normal single-statement pipeline** — `_run_query_body` for
jobs, the interpreter's `_run_query` for scripts — so row-access policies,
materialized-view refresh, wildcard-table expansion, and every other rewrite
apply to the exported query exactly as they would to a bare `SELECT`; only then
is the materialised result written.

Detection works off the parsed AST because SQLGlot **discards the `OPTIONS`** when
transpiling `EXPORT DATA` to DuckDB while the `exp.Export` node still carries
them. This is preferred over (a) extending the substring-based
`_UNSUPPORTED_KEYWORDS` reject (could only reject, not execute), (b) intercepting
inside `_run_single_sql` (the scripting path has its own `_run_query`, and
threading the export row/file counts back out for statistics is awkward), and
(c) a new REST job type (`EXPORT DATA` is a query-job SQL statement that must
also work in scripts).

### 2. Reuse the extract writer via a shared `_copy_relation_to_file` helper

The format → `COPY (…) TO '…' (FORMAT …)` dispatch currently inlined in
`execute_extract_job` is refactored into a shared helper that takes a DuckDB
relation SQL, a resolved destination path, a format, and an options mapping, and
returns the rows written. It carries the `avro`-extension missing-dependency
handling unchanged. Both `execute_extract_job` (single file) and the new
`execute_export_data` (per shard) call it. URI resolution reuses `_resolve_uri`;
path safety reuses `_validate_local_path`; parent directories are created before
the write.

The inner SELECT is translated through the **existing** pipeline
(`SQLTranslator.translate` → `rewrite_table_refs` → `bind_parameters`), so all
BigQuery→DuckDB rules, qualified-name rewrites, and query parameters apply to the
exported query unchanged.

### 3. Real size-based sharding with a configurable threshold and an `nbytes` proxy

A single `*` wildcard in the URI is replaced by a zero-based, 12-digit,
left-padded counter (`…000000000000`, …), matching BigQuery. The result is
materialized once (`ctx.engine.fetch_arrow`) and sliced into contiguous
row-ranges (preserving `ORDER BY`):
`shard_count = max(1, ceil(table.nbytes / threshold))`.

The threshold is a new setting `export_shard_threshold_bytes`
(`BQEMU_EXPORT_SHARD_THRESHOLD_BYTES`), default ≈1 GiB so realistic exports
produce one file (parity), overridable to a small value for deterministic
multi-shard tests. **`pyarrow.Table.nbytes` (in-memory) is an explicit
approximation** of compressed on-disk size; the emulator's shard boundaries can
differ from BigQuery's at the margin. This is accepted because (a) BigQuery's
true >1 GB threshold cannot be feasibly recorded, and (b) emulator workloads are
small — the common case is one shard, which *is* recorded against real BigQuery.
A wildcard-free URI writes one file and errors if the output exceeds the
threshold, mirroring BigQuery's "use a wildcard" requirement.

### 4. Cloud Storage only; `WITH CONNECTION` rejected

`EXPORT DATA WITH CONNECTION` (Amazon S3 / Azure Blob / Pub/Sub reverse-ETL) is
rejected with a clear `UnsupportedFeatureError`. The emulator's charter is
BigQuery and its Cloud Storage integration; external sinks are separate services
real BigQuery treats as out-of-band.

`ORC` export is **not** modelled as an unsupported feature. BigQuery does not
export ORC, but the conformance recording
(`sql_corpus/export_data/export_orc_rejected`) shows it rejects `format='ORC'`
exactly the way it rejects any unrecognised value — as an invalid `format`
OPTIONS value: `InvalidQueryError` (`invalidQuery`, HTTP 400,
`location = "query"`), with the message `'ORC' is not a valid value; failed to
set 'format' in EXPORT DATA OPTIONS`. ORC therefore carries no special case in
`_normalize_export_format`; it falls through the same path as `format='XML'`.

### 5. `EXPORT_DATA` result + statistics, pinned by recording

`_execute_export_data_job` stores a zero-row result and builds the job
statistics via `_build_query_statistics(statement_type="EXPORT_DATA",
export_statistics=(file_count, row_count))`. The recorded job resource
(`http_corpus/jobs/export_csv_query_job`) pins the field set, so the emulator
emits `statistics.query.statementType = "EXPORT_DATA"` plus
`exportDataStatistics = {fileCount, rowCount}` (int64-strings, sourced from the
`_ExportOutcome`'s written-file and exported-row counts) and the sibling
`totalPartitionsProcessed` / `transferredBytes` fields BigQuery reports for an
export job. The `<*>`-recorded query fields (`queryPlan`, `timeline`,
`totalBytesBilled`, …) are intentionally left unset — the conformance
comparator treats a wildcard leaf as "absent or present". The error envelopes
(`invalidQuery` / HTTP 400 for an invalid `format` value and for a missing or
empty `uri`) are likewise taken from recorded fixtures, not hand-authored.

## Consequences

### Capability matrix shift

| Surface | Before | After |
|---|---|---|
| `EXPORT DATA` → CSV / JSON / PARQUET / AVRO (GCS) | ❌ silently mangled → DuckDB error | ✅ |
| `EXPORT DATA` size-based wildcard sharding | ❌ | ✅ (emulator-scale `nbytes` proxy) |
| `EXPORT DATA` inside `BEGIN … END` scripts | ❌ | ✅ (shared path) |
| `EXPORT DATA WITH CONNECTION` (S3 / Blob / Pub/Sub) | ❌ | ❌ (out of scope, clear error) |
| `EXPORT DATA … FORMAT 'ORC'` | ❌ | ❌ (matches BigQuery) |
| `extract` job behavior | unchanged | unchanged (now shares the writer) |

### Coverage + test surface

- Unit tests (`tests/unit/jobs/`): options parsing/validation; every format;
  CSV header/delimiter; per-format compression; `overwrite` true/false; shard
  boundaries (0 / 1 / exactly-threshold / multi-shard via low threshold); every
  error path; `WITH CONNECTION` rejection; `EXPORT DATA` inside a script.
- Property tests (`tests/property/`, Hypothesis): `sum(shard rows) == SELECT
  rows`; shard-count monotonic in row count for a fixed threshold.
- Conformance: SQL + HTTP corpus `export_*` fixtures (single-shard), recorded
  from real BigQuery; a `ddl.export_data` `SurfaceItem` and a regenerated
  coverage matrix.
- E2E: one scenario per client × five clients (create → `EXPORT DATA` → poll
  DONE → assert shard file(s) under the mounted GCS root → read back).

### Configuration

A new `export_shard_threshold_bytes` setting on the existing `Settings` surface
(`BQEMU_EXPORT_SHARD_THRESHOLD_BYTES`), alongside `gcs_local_root`.

### Error envelope

New validation branches in the export path produce BigQuery-shaped
`InvalidQueryError` / `UnsupportedFeatureError`; the existing `error_mapper`
chain is preserved for the inner SELECT's execution errors.

## Unresolved questions

These are accepted, documented limitations rather than blockers: the
recorded conformance corpus is green without them, and none changes the
externally observable contract for the formats the corpus exercises.

- **AVRO `compression` is validated but not forwarded.** The value is
  checked against the per-format allow-list (`DEFLATE` / `SNAPPY` /
  `NONE`), but `_build_copy_clause` emits no codec option for
  `FORMAT AVRO` because DuckDB's `avro` `COPY` writer exposes none. AVRO
  output is written uncompressed regardless of the requested codec. CSV,
  JSON, and PARQUET compression *are* forwarded to DuckDB and applied.
  Closing this needs either an upstream DuckDB `avro` codec option or a
  post-write re-encode step.
- **`use_avro_logical_types` is validated but not applied.** It is
  accepted on AVRO exports (and rejected on other formats), but does not
  yet influence the written Avro schema.

Both are surfaced to users in the
[exporting data guide](../guides/exporting-data.md#limitations).

## Alternatives considered

- **Parallel export writer** (not reusing extract) — rejected: duplicates the
  format dispatch and risks output drift between export and extract.
- **Translator keyword-reject only** — rejected: cannot execute the statement,
  only fail it; leaves the parity gap open.
- **New REST `export` job type** — rejected: `EXPORT DATA` is a query-job SQL
  statement and must also work in scripts; a job type misses both.
- **Always single-shard** / **lenient literal path** — rejected per RFC 0001 in
  favor of faithful BigQuery file-count and naming parity.
- **Byte-exact / streaming sharding** — deferred: unjustified complexity at
  emulator scale; tabled in RFC 0001's future possibilities.

## Related work

- [ADR 0027](0027-load-extract-avro-orc.md) — load/extract Avro/ORC; source of
  the reused `COPY` writer and the ORC-export exclusion precedent.
- [ADR 0022](0022-conformance-corpus-design.md) /
  [ADR 0023](0023-conformance-divergence-baseline.md) — conformance corpus +
  divergence baseline the `export_*` fixtures plug into.
- [ADR 0015](0015-scripting-execution-model.md) — scripting dispatch that routes
  scripted `EXPORT DATA`.
- [ADR 0020](0020-admin-import-export.md) — admin backup via DuckDB
  `EXPORT DATABASE`; unrelated to this GoogleSQL `EXPORT DATA` (name collision
  noted).

## References

- [BigQuery: Export statements (GoogleSQL)](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/export-statements)
- [BigQuery: Export table data to Cloud Storage](https://docs.cloud.google.com/bigquery/docs/exporting-data)
- [BigQuery: export formats and compression types](https://docs.cloud.google.com/bigquery/docs/exporting-data#export_formats_and_compression_types)
- [DuckDB `COPY` statement](https://duckdb.org/docs/sql/statements/copy)
- [DuckDB `avro` extension](https://duckdb.org/docs/extensions/avro)
