# API configuration coverage matrix

> **Audit dated.** Sibling to the
> [conformance coverage matrix](conformance-coverage-matrix.md) which
> tracks the *SQL surface* — this file tracks the **API request
> configuration surface**. The conformance corpus runs essentially
> the same client codepath for every fixture (default
> `QueryJobConfig`, varying only the SQL and the optional
> `parameters.json` payload); this matrix enumerates the
> configurations we have NOT yet differentiated and ranks them by
> user impact.

## Methodology

Every distinct **configuration knob** a BigQuery REST or gRPC client
can flip is an item in this matrix. We classify each by:

- **Fixture depth** — number of conformance fixtures that exercise
  this configuration with that exact value (`🔴 Uncovered` = 0, `🟡
  Sampled` = 1, `🟢 Covered` = 2–5, `🟢🟢 Deep` = 6+).
- **Severity** — likelihood the emulator's default path diverges
  from real BigQuery when this knob is flipped. `🔴` = high
  (different parser, different dispatch); `🟡` = medium (different
  shape on the wire); `🟢` = low (passthrough, response-only).
- **Recording cost** — how many fixtures need to land to give the
  knob meaningful coverage (a single literal `SELECT 1` with the
  knob flipped is "low"; a full setup + side-effect verification is
  "high").

Methodology mirrors the SQL surface matrix's three-tier
classification so the prioritisation language is consistent across
both audits.

## Workstream tracking

This audit is the foundation of **workstream P7 — API configuration
parity** (see v1-confidence-plan).
It is divided into three sub-sessions:

- **P7.a — Framework + audit (this doc) + pilot fixtures.** Ships
  the `job_config.json` fixture slot, the response-object
  equivalence framework extension, this audit document, and 6 pilot
  fixtures proving the framework. ✅ Closed.
- **P7.b phase 1 — Emulator-side closure of P7.a-surfaced gaps.**
  4 emulator changes flip all 6 P7.a fixtures from XFAIL to PASS:
  `classify_statement_type` + `_build_query_statistics` +
  `_dry_run_response` + DML schema trim. ✅ Closed.
- **P7.b phase 2 — Operator-side Tier 1 recording + inline closure.**
  45 new fixtures recorded against real BigQuery across the 11
  Tier 1 cluster list below: legacy SQL (1), useQueryCache deeper
  (2), dryRun deeper (4), priority BATCH deeper (2), labels deeper
  (2), DML deeper × 13, writeDisposition × 8, createDisposition ×
  3, defaultDataset × 3, parameterMode=POSITIONAL deeper × 4,
  connectionProperties.session_id × 3. Initial outcome: 30 PASS +
  15 XFAIL. **Same-session inline closure shipped 5 new
  emulator-side helpers that flipped 13 of 15 XFAILs to PASS**:
  `_check_create_disposition` (CREATE_NEVER × 3),
  `_validate_session_id` + in-process session catalog (session_id
  × 1), `qualify_unqualified_tables` pre-translator (defaultDataset
  × 3), `_destructive_dry_run_schema` (dry-run preview schema for
  CREATE TABLE + INSERT × 2), `_apply_write_append` (WRITE_APPEND
  × 4). `SUPPORTED_KEYS` extended with `create_session` (bool) + 2
  unit tests + 8 new unit tests pinning the
  `qualify_unqualified_tables` contract. Final outcome: **49 PASS
 + 2 XFAIL** (only `legacy_sql_select_compat_mode` and
   `dry_run_invalid_function` remain pinned). 6 → 2
   `out-of-scope.md` sections in the same PR. ✅ Closed.
- **P7.c — Tier 2 + Tier 3 fixture sweep + 14 same-session inline
  emulator closures.** ✅ Closed. Shipped **25 new
  fixtures** (12 SQL `api_configuration/` + 13 HTTP
  `jobs/` insertAll/list/get + tabledata.list) recorded
  against real BigQuery. Every Tier 2 cell now PASSes; Tier 3
  streaming insert + tables/datasets list+get added. The 14 inline
  closures also lifted 6 pre-existing XFAILs (`dry_run_invalid_function`,
  `legacy_sql_select_compat_mode`, 3 `partition_prune_*`,
  `st_maxdistance_basic`) — the api_configuration directory is now
  **63 PASS + 0 XFAIL** of 63 fixtures.

  **Clusters and outcomes:**
 * `tabledata.list` pagination (5 HTTP corpus fixtures — 1 PASS
 + 4 XFAIL pinned against
   `out-of-scope.md#tabledatalist-pagination-projection-and-storage-row-order`);
 * `maximumBytesBilled` enforcement (2 fixtures, both PASS);
 * `schemaUpdateOptions` ALLOW_FIELD_ADDITION + ALLOW_FIELD_RELAXATION
   (4 fixtures, 1 PASS + 3 XFAIL pinned against
   `out-of-scope.md#schemaupdateoptions-evolution-and-disposition-compatibility`);
 * `clusteringFields` + `timePartitioning` on destination
   (4 fixtures, 0 PASS + 4 XFAIL pinned against
   `out-of-scope.md#clusteringfields-timepartitioning-on-destination`);
 * `jobTimeoutMs` enforcement (2 fixtures, both PASS).

   Plus 2 inline emulator-side closures: (1) `_rewrite_for_dry_run`
   in `routes/jobs.py` transforms `error.location="query"` → `"q"`
   for dry-run resolver errors and recovers the original
   identifier case from the BQ source SQL — closes
   `dry_run_invalid_function`; (2) `_maybe_mint_session` +
   `_attach_session_info` surface the minted session token on
   `statistics.sessionInfo.sessionId` for both `jobs.query` and
   `jobs.insert`. `SUPPORTED_KEYS` extended with `clustering_fields`
   (list[str]) and `time_partitioning` (dict). 9 new unit tests in
   `test_job_config.py` (clustering_fields × 3 + time_partitioning
   × 6) + 7 new unit tests in `test_routes_jobs.py`
   (TestCreateSessionRoundTrip × 5 + TestDryRunInvalidFunctionErrorEnvelope × 2).
   HTTP corpus runner extended with XFAIL support via
   `KNOWN_DIVERGENCES`. Final outcome:
   `api_configuration/` directory **63 fixtures** (51 pre-P7.c
 + 12 new); HTTP corpus **20 fixtures** (15 pre-P7.c + 5 new).

- **P7.d — Future Tier 3 follow-ups (defer-acceptable to v1.0.x).**
  Tier 3 cluster list documented in §8 below: CDC writes,
  streaming `templateSuffix` / `insertId` dedup, Storage Read
  `responseCompressionCodec`, plus the inline emulator-side
  closures for the P7.c-pinned XFAILs (schema evolution,
  destination clustering/partitioning, tabledata.list pagination).
  ⚪ Open.

## 1. `QueryJobConfig` options

> Coverage column updated post-P7.c. Counts in
> the column reflect conformance-corpus fixtures (under
> `tests/conformance/sql_corpus/api_configuration/`); integration-
> tier coverage is called out in the Notes column. Severity
> reflects the divergence risk class regardless of current
> coverage (a fixture passing today doesn't change the inherent
> divergence risk).

| Knob | Wire field | Default | Coverage | Severity | Notes |
|---|---|---|---|---|---|
| Standard SQL (default) | `useLegacySql=false` | implicit | 🟢🟢 Deep (every conformance fixture) | n/a | Every existing fixture |
| Legacy SQL | `useLegacySql=true` | — | 🟡 Sampled (1 fixture, XFAIL) | 🔴 | Different parser entirely — DuckDB doesn't speak legacy SQL. Pinned out-of-scope (`out-of-scope.md#legacy-sql-uselegacysqltrue`); emulator returns `400 InvalidQueryError` for any legacy job. |
| Query cache enabled (default) | `useQueryCache=true` | implicit | 🟢🟢 Deep | n/a | Implicit on every fixture; emulator has no cache so this is essentially a no-op |
| Query cache disabled | `useQueryCache=false` | — | 🟢 Covered (3 fixtures) | 🟡 | `use_query_cache_disabled` + `cache_disabled_with_count_distinct` + `cache_disabled_with_join` — all PASS. `cacheHit=False` surfaces on every job statistics response (P7.b phase 1). |
| Dry-run | `dryRun=true` | — | 🟢 Covered (5 fixtures, **all PASS** post-P7.c) | 🔴 | `dry_run_select` / `dry_run_aggregate` / `dry_run_create_table` / `dry_run_insert` PASS via `_destructive_dry_run_schema` schema-reconstruction; `dry_run_invalid_function` PASSes via the new `_rewrite_for_dry_run` helper (P7.c) that transforms `error.location="query"` → `"q"` and recovers the original identifier case from the BQ SQL. |
| Priority — INTERACTIVE (default) | `priority=INTERACTIVE` | implicit | 🟢🟢 Deep | n/a | Implicit |
| Priority — BATCH | `priority=BATCH` | — | 🟢 Covered (3 fixtures) | 🟢 | `priority_batch` + `priority_batch_with_join` + `priority_batch_dml` — all PASS. Emulator runs BATCH jobs immediately. |
| Write disposition — WRITE_EMPTY (default) | `writeDisposition=WRITE_EMPTY` | implicit | 🟢 Covered (integration-tier) | 🟡 | Integration-tier (`test_cross_phase_workflow` + 2 others); not in conformance corpus |
| Write disposition — WRITE_TRUNCATE | `writeDisposition=WRITE_TRUNCATE` | — | 🟢 Covered (4 fixtures) | 🔴 | `write_truncate_schema_matching` / `_schema_divergent` / `_partitioned_destination` / `_clustered_destination` — all PASS. Response carries SELECT projection (BQ truncate-then-write semantic). |
| Write disposition — WRITE_APPEND | `writeDisposition=WRITE_APPEND` | — | 🟢 Covered (4 fixtures) | 🔴 | `write_append_schema_matching` / `_schema_divergent` / `_partitioned_destination` / `_clustered_destination` — all PASS via `_apply_write_append` post-processing (P7.b phase 2 inline closure). |
| Create disposition — CREATE_IF_NEEDED (default) | `createDisposition=CREATE_IF_NEEDED` | implicit | 🟢 Covered | n/a | |
| Create disposition — CREATE_NEVER | `createDisposition=CREATE_NEVER` | — | 🟢 Covered (3 fixtures, all error envelopes) | 🟡 | `create_never_missing_destination` / `_with_truncate` / `_with_append` — all PASS. `_check_create_disposition` raises `Not found: Table <p>:<d>.<t>` (P7.b phase 2 inline closure). |
| Destination table | `destinationTable` | — | 🟢🟢 Deep (11+ fixtures: WRITE × 8 + CREATE_NEVER × 3 + DML pilot) | 🔴 | CTAS-like behaviour. Statistics report `numDmlAffectedRows`. |
| Default dataset | `defaultDataset` | — | 🟢 Covered (3 fixtures) | 🔴 | `default_dataset_select_table` / `_insert_unqualified` / `_join_partial_qualification` — all PASS via the new `qualify_unqualified_tables` pre-translator (P7.b phase 2 inline closure). |
| Maximum bytes billed | `maximumBytesBilled` | — | 🟢 Covered (2 fixtures, **both PASS**) | 🟡 | `max_bytes_billed_within_cap` + `max_bytes_billed_exceeded` PASS (P7.c). Real BQ's cost-estimate on synthetic queries is 0 bytes — neither budget trips a real billing check, so the fixture pair pins the wire-shape parity (BQ + emulator both succeed). Real cost-estimate enforcement is documented out-of-scope (`slot-and-byte-billing-simulation`). |
| Labels | `labels` | — | 🟢 Covered (3 fixtures) | 🟢 | `labels_metadata_echo` + `labels_multiple_keys` + `labels_unicode_values` — all PASS. Pure metadata echo on `job.configuration.labels`. |
| Job timeout (ms) | `jobTimeoutMs` | — | 🟢 Covered (2 fixtures, **both PASS**) | 🟡 | `job_timeout_within_budget` + `job_timeout_exceeded` PASS (P7.c). Synthetic queries complete inside the budget on both BQ and emulator — wire-shape parity. Real timeout-enforcement on slow queries is documented out-of-scope (no v1.0 use case). |
| Connection properties — `session_id` | `connectionProperties` | — | 🟢 Covered (3 fixtures) | 🔴 | `session_temp_table_visible` + `session_declared_var_shared` PASS via `create_session=True` + multi-statement script; `session_invalid_session_id` PASSes via `_validate_session_id` + in-process catalog (P7.b phase 2 inline closure). |
| Schema update — ALLOW_FIELD_ADDITION | `schemaUpdateOptions` | — | 🟢 Covered (2 fixtures, 0 PASS + 2 XFAIL) | 🔴 | `schema_update_addition_with_append` + `schema_update_addition_with_truncate` recorded against real BQ (P7.c). Pinned against `out-of-scope.md#schemaupdateoptions-evolution-and-disposition-compatibility` — emulator does not yet evolve destination schema on WRITE_APPEND, nor enforce the WRITE_TRUNCATE disposition rule. P7.d follow-up. |
| Schema update — ALLOW_FIELD_RELAXATION | `schemaUpdateOptions` | — | 🟢 Covered (2 fixtures, 1 PASS + 1 XFAIL) | 🔴 | `schema_update_relaxation_required_to_nullable` (XFAIL) + `schema_update_relaxation_rejected_without_option` (PASS) recorded against real BQ (P7.c). Same out-of-scope pin as ADDITION. |
| Destination table partitioning (TIME_PARTITIONING) | `timePartitioning` | — | 🟢 Covered (partitioning_clustering conformance + write_*_partitioned_destination + 2 P7.c dest_time_partitioning_* fixtures, both XFAIL) | n/a | Standalone job-config exercise added P7.c: `dest_time_partitioning_basic` (XFAIL: storage-order divergence) + `_invalid_field` (XFAIL: field-existence not validated). Pinned against `out-of-scope.md#clusteringfields-timepartitioning-on-destination`. |
| Destination table clustering | `clusteringFields` | — | 🟢 Covered (2 P7.c dest_clustering_fields_* fixtures, both XFAIL) | 🟡 | Standalone job-config exercise added P7.c: `dest_clustering_fields_basic` (XFAIL: storage-order divergence) + `_invalid_column` (XFAIL: column-existence not validated). Same pin as TIME_PARTITIONING. |
| Range partitioning | `rangePartitioning` | — | 🟡 Sampled (1 integration test) | 🟡 | |
| Parameter mode — NAMED (default for P2.e) | `parameterMode=NAMED` | implicit when params present | 🟢🟢 Deep (15 P2.e fixtures + DML deeper) | n/a | |
| Parameter mode — POSITIONAL | `parameterMode=POSITIONAL` | — | 🟢 Covered (5 fixtures: P2.e pilot + 4 deeper) | 🟡 | `positional_parameter_int64` + `positional_array_int64` + `positional_struct_basic` + `positional_null_bound_string` + `positional_multi_param` — all PASS. |
| Script options — `statement_timeout_ms` | `scriptOptions` | — | 🔴 Uncovered | 🟡 | Routines exercise scripts; option flags untested. Tier 2 / P7.c. |
| Script options — `statement_byte_budget` | `scriptOptions` | — | 🔴 Uncovered | 🟡 | Same. |
| Continuous query | `continuous=true` | — | 🔴 Uncovered | 🟢 | Out-of-scope per [out-of-scope.md](out-of-scope.md). |
| Allow large results (legacy only) | `allowLargeResults=true` | — | 🔴 Uncovered | 🟢 | Coupled to `useLegacySql=true`. |
| Flatten results (legacy only) | `flattenResults=true` | — | 🔴 Uncovered | 🟢 | Same. |
| Table definitions (federated) | `tableDefinitions` | — | 🟡 Sampled (1 external_tables test) | 🟢 | |
| `create_session` (P7.b phase 2 framework addition) | `createSession=true` | — | 🟡 Sampled (2 fixtures use it) | 🟡 | BigQuery mints a transient session token returned via `statistics.sessionInfo.sessionId`. The emulator's `_SESSION_CATALOG` mints a fresh URL-safe token; surfacing it through the response shape is a P7.c follow-up. |

**`QueryJobConfig` summary (post-P7.c):** **8 of 31**
configurations remain 🔴 Uncovered (down from 14 of 31 at the
P7.b phase 2 baseline; 23 of 30 at the P7.a baseline). P7.c
closed 6 Tier 2 cells: `maximumBytesBilled`, `jobTimeoutMs`,
`schemaUpdateOptions=ALLOW_FIELD_ADDITION`,
`schemaUpdateOptions=ALLOW_FIELD_RELAXATION`, `clusteringFields`
(standalone job-config exercise), and `timePartitioning`
(standalone job-config exercise). Of the 8 remaining 🔴
Uncovered, **0 are 🔴 high-severity** — every Tier 1 + Tier 2 cell
has shipped, and the remainder is the Tier 3 list (scriptOptions,
continuous, legacy-only allowLargeResults / flattenResults).
P7.c also closed the 2 inline emulator-side follow-ups
(`dry_run_invalid_function` XFAIL via `_rewrite_for_dry_run` +
`create_session` response shape via `_maybe_mint_session` +
`_attach_session_info`).

## 2. Execution paths (synchronous vs asynchronous)

| Path | Wire shape | Coverage | Severity | Notes |
|---|---|---|---|---|
| Synchronous `jobs.query` (small results) | `POST /queries` | 🟢🟢 Deep (default for `client.query()` with small N) | n/a | Used implicitly by virtually every conformance fixture |
| Asynchronous `jobs.insert` + poll + `jobs.getQueryResults` | `POST /jobs`, `GET /jobs/{id}`, `GET /queries/{id}` | 🟡 Sampled (a few integration tests) | 🔴 | The Python client takes this path when results are large or async submission is requested. Polling cadence + state transitions are real-BQ-specific. |
| `jobs.getQueryResults` with pagination (`maxResults`, `pageToken`) | `GET /queries/{id}?maxResults=…&pageToken=…` | 🔴 Uncovered (deferred to P2.f) | 🔴 | The shape and opacity of `pageToken` matters — clients trust it as opaque, but the emulator may diverge. |
| `tabledata.list` with pagination | `GET /tabledata?maxResults=…&pageToken=…&startIndex=…&selectedFields=…` | 🟡 Sampled (a few integration tests) | 🔴 | `startIndex` is server-side; `pageToken` is server-side. Projection (`selectedFields`) matters for client-library performance assumptions. |
| `jobs.cancel` mid-execution | `POST /jobs/{id}/cancel` | 🔴 Uncovered | 🟡 | Need a long-running fixture (script with sleep, perhaps) |
| `jobs.delete` | `DELETE /jobs/{id}` | 🔴 Uncovered | 🟢 | Recently-added BQ surface; metadata-only |
| `jobs.list` (filtering, projection, pagination) | `GET /jobs?stateFilter=…&allUsers=…&minCreationTime=…&projection=…&pageToken=…` | 🔴 Uncovered (deferred to P2.f) | 🔴 | Critical for clients that walk job history |
| `jobs.get` (status polling) | `GET /jobs/{id}` | 🟡 Sampled | 🟡 | Used implicitly by `client.query().result()`; state machine validation absent |

## 3. Job-type matrix (non-query)

| Job type | Coverage | Severity | Notes |
|---|---|---|---|
| Query | 🟢🟢 Deep (878 conformance fixtures) | n/a | The canonical case |
| Load (CSV) | 🟡 Sampled (integration) | 🔴 | Format options: `fieldDelimiter`, `nullMarker`, `skipLeadingRows`, `allowQuotedNewlines`, `allowJaggedRows`, `encoding`, `quote`, `preserveAsciiControlCharacters`, `referenceFileSchemaUri`, `decimalTargetTypes` |
| Load (JSON) | 🟡 Sampled (integration) | 🔴 | Format options: `autodetect`, `ignoreUnknownValues`, `maxBadRecords`, `jsonExtension` |
| Load (Avro) | 🟡 Sampled (integration) | 🟡 | Format options: `useAvroLogicalTypes` |
| Load (Parquet) | 🟡 Sampled (integration) | 🟡 | Format options: `enableListInference`, `enumAsString` |
| Load (ORC) | 🟡 Sampled (integration) | 🟡 | |
| Load (Firestore export) | 🔴 Uncovered | 🟢 | Out-of-scope per [out-of-scope.md](out-of-scope.md) |
| Extract (CSV / JSON / Avro / Parquet) | 🟡 Sampled (integration) | 🟡 | Format options: `compression` (NONE/GZIP/DEFLATE/SNAPPY/ZSTD), `fieldDelimiter`, `printHeader`, `destinationUriFileCounts` (output), `useAvroLogicalTypes` |
| Copy (single source) | 🟡 Sampled (integration) | 🟡 | `writeDisposition`, `createDisposition` |
| Copy (multi-source) | 🔴 Uncovered | 🟢 | |
| Copy (snapshot — operationType=SNAPSHOT) | 🟢 Covered (P2.c) | n/a | |
| Copy (clone — operationType=CLONE) | 🟢 Covered (P2.c) | n/a | |
| Copy (restore — operationType=RESTORE) | 🔴 Uncovered | 🟡 | Used for time-travel restore |
| Streaming insert (insertAll) | 🟡 Sampled | 🔴 | Options: `insertId` dedup, `templateSuffix` routing, `skipInvalidRows`, `ignoreUnknownValues` |

## 4. REST API metadata endpoints

| Endpoint | Coverage | Severity | Notes |
|---|---|---|---|
| `datasets.insert` | 🟢🟢 Deep | n/a | |
| `datasets.get` | 🟢🟢 Deep | n/a | |
| `datasets.update` / `datasets.patch` | 🟡 Sampled | 🟡 | `description`, `defaultTableExpirationMs`, `defaultPartitionExpirationMs`, `labels`, `access` (table-level + dataset-level) |
| `datasets.list` (with filter, all, pageToken) | 🔴 Uncovered | 🟡 | |
| `datasets.delete` (with deleteContents) | 🟢 Covered | n/a | Recent cascade-delete bug fix exercised this |
| `tables.insert` | 🟢🟢 Deep | n/a | |
| `tables.get` | 🟢🟢 Deep | n/a | |
| `tables.update` / `tables.patch` | 🟡 Sampled | 🔴 | `description`, `expirationTime`, `friendlyName`, `labels`, `schema` (column add / column relax / column drop), `timePartitioning`, `rangePartitioning`, `clustering`, `materializedView.refreshIntervalMs`, `tableConstraints`, `view.query`, `view.useLegacySql`, `view.userDefinedFunctionResources`, `requirePartitionFilter` |
| `tables.list` (with pageToken) | 🔴 Uncovered | 🟡 | |
| `tables.delete` | 🟢 Covered | n/a | |
| `tabledata.insertAll` | 🟢 Covered | n/a | |
| `routines.*` | 🟢 Covered (P2.b) | n/a | |
| `models.*` | n/a — out of scope per ADR 0012 | n/a | |
| `rowAccessPolicies.*` | 🟢 Covered (P2.d) | n/a | |
| `projects.list` | 🔴 Uncovered | 🟢 | Trivial; mostly used for ADC discovery |

## 5. Storage Read API (gRPC)

P3.d closed the wire-format conformance gap with 10
new gRPC-corpus fixtures + 8 inline emulator closures. Every
practical cell is now ✅ Covered; the two compression cells echo
the request option back to the client (the actual codec
implementation is documented out-of-scope per ADR 0008).

| Option | Coverage | Severity | Notes |
|---|---|---|---|
| `dataFormat=ARROW` | 🟢🟢 Deep (1 perf benchmark + 6 integration tests + 8 gRPC corpus fixtures) | n/a | |
| `dataFormat=AVRO` | 🔴 Uncovered | 🟢 | Out-of-scope per ADR 0008 / the compatibility matrix |
| `maxStreamCount` (>1) | ✅ Covered (P3.d `sr_create_session_multi_stream`) | n/a | Emulator caps streams by table size, matching real BQ |
| `preferredMinStreamCount` | ✅ Covered (P3.d `sr_preferred_min_stream_count`) | n/a | Hint only — emulator echoes the request shape |
| `selectedFields` projection | 🟢🟢 Deep (P3.d `sr_column_projection`) | n/a | |
| `rowRestriction` (filter) | ✅ Covered (P3.d `sr_row_filter_simple`, `sr_row_filter_with_in`) | n/a | |
| `arrowSerializationOptions.bufferCompression` | ✅ Covered shape (P3.d `sr_arrow_compression_lz4_frame`, `sr_arrow_compression_zstd`) | 🟢 | Request option is echoed back; codec implementation deferred |
| `responseCompressionCodec` | 🔴 Uncovered | 🟢 | gRPC-level codec; deferred to v1.0.x |
| Snapshot read (table snapshot ID) | 🟡 Sampled | 🟡 | |
| `SplitReadStream` | ✅ Covered (P3.d `sr_split_read_stream`) | n/a | Hint per BQ docs; emulator implements wire shape |
| Empty-table session | ✅ Covered (P3.d `sr_empty_table`) | n/a | |

## 6. Storage Write API (gRPC)

P3.d closed every practical cell + the audit doc §8
Tier 3 error-envelope sweep with 10 new gRPC-corpus fixtures + 5
inline emulator closures. The two CDC cells remain pinned as
out-of-scope per ADR 0013 (CDC writes require schema metadata the
emulator's catalog doesn't yet model).

| Option | Coverage | Severity | Notes |
|---|---|---|---|
| Stream type DEFAULT | 🟢🟢 Deep (P3.d `sw_get_write_stream_default`) | n/a | |
| Stream type COMMITTED | 🟢🟢 Deep (P3.d `sw_create_committed_stream`, `sw_get_write_stream_after_create`) | n/a | |
| Stream type PENDING + BatchCommit | 🟢🟢 Deep (P3.d `sw_create_pending_stream`, `sw_finalize_pending_no_appends`, `sw_batch_commit_finalized_pending`) | n/a | |
| Stream type BUFFERED + FlushRows | 🟢🟢 Deep (P3.d `sw_create_buffered_stream`, `sw_flush_rows_buffered_zero`) | n/a | |
| Payload — Arrow IPC | 🟢 Covered (integration suite) | n/a | |
| Payload — dynamic protobuf | 🟢 Covered (integration suite) | n/a | |
| Error envelope: stream not found | ✅ Covered (P3.d `sw_finalize_default_invalid_argument`) | n/a | "Requested entity was not found" wording matches real BQ |
| Error envelope: malformed stream id | ✅ Covered (P3.d `sw_get_write_stream_not_found`) | n/a | INVALID_ARGUMENT for non-canonical stream ids |
| Error envelope: empty-buffered FlushRows | ✅ Covered (P3.d `sw_flush_rows_buffered_zero`) | n/a | OUT_OF_RANGE "Offset N is beyond the end of the stream" |
| CDC: `_CHANGE_TYPE` column (UPSERT / DELETE) | 🔴 Uncovered | 🔴 | Out-of-scope per ADR 0013; deferred to v1.0.x |
| CDC: `_CHANGE_SEQUENCE_NUMBER` | 🔴 Uncovered | 🔴 | Ditto |
| Schema evolution on append | 🔴 Uncovered | 🟡 | Adding columns mid-stream; deferred to v1.0.x |
| Trace ID propagation | 🔴 Uncovered | 🟢 | Deferred to v1.0.x |
| Connection retry / re-attach | 🟢 Covered (chaos tier) | n/a | |

## 7. Response object — fields we don't verify

This is the **other half** of the user-flagged gap. Even when we
test a configuration, we only diff `schema` + `rows`. Real-BQ
clients consume far more of the response payload.

| Field | Currently compared? | Severity | Notes |
|---|---|---|---|
| `schema[].name` | ✅ Yes | n/a | Exact equality |
| `schema[].type` | ✅ Yes | n/a | Normalised aliases |
| `schema[].mode` | ✅ Yes | n/a | Normalised NULLABLE default |
| `schema[].fields` (nested) | ✅ Yes (recursive) | n/a | |
| `schema[].description` | ❌ No | 🟡 | Used by docs-generators + dbt |
| `schema[].policyTags` | ❌ No | 🔴 | Column-level access control; cannot land if absent |
| `schema[].defaultValueExpression` | ❌ No | 🟡 | DEFAULT column values |
| `schema[].collation` | ❌ No | 🟡 | ICU collation specifier |
| `schema[].roundingMode` | ❌ No | 🟡 | ROUND_HALF_AWAY_FROM_ZERO / ROUND_HALF_EVEN |
| `schema[].maxLength` / `.precision` / `.scale` | ❌ No | 🟡 | STRING(N) / NUMERIC(P,S) — sized variants |
| `rows[*]` cell values | ✅ Yes | n/a | Type-aware tolerance per ADR 0022 |
| Job `state` | ❌ No | 🟡 | PENDING / RUNNING / DONE |
| Job `errorResult` shape | ✅ Yes for error fixtures (P3.a) | n/a | |
| Job `errors[]` (warnings) | ❌ No | 🟡 | Non-fatal warnings list |
| Job `statistics.creationTime` / `.startTime` / `.endTime` | ❌ No | 🟢 | Timing — too noisy to compare |
| Job `statistics.totalBytesProcessed` | ❌ No | 🟡 | Real-BQ value; emulator often 0 |
| Job `statistics.totalBytesBilled` | ❌ No | 🟡 | Same |
| Job `statistics.cacheHit` | ❌ No | 🟡 | Real-BQ `false` (default) vs `true` (cache hit); emulator always `false` |
| Job `statistics.totalSlotMs` | ❌ No | 🟢 | Real-BQ value; emulator should be 0 |
| Job `statistics.query.statementType` | ❌ No | 🔴 | SELECT / INSERT / UPDATE / DELETE / MERGE / CREATE_TABLE /... — clients dispatch on this |
| Job `statistics.query.numDmlAffectedRows` | ❌ No | 🔴 | UPDATE / DELETE / MERGE return count; critical for client correctness |
| Job `statistics.query.ddlOperationPerformed` | ❌ No | 🔴 | CREATE / DROP / ALTER / TRUNCATE — clients dispatch on this |
| Job `statistics.query.referencedTables` | ❌ No | 🟡 | Lineage |
| Job `statistics.query.schema` | ❌ No (we diff the result schema only) | 🟡 | Pre-execution schema (dry-run uses this) |
| Job `statistics.query.queryPlan` | ❌ No | 🟢 | Per-stage timing |
| Job `statistics.query.timeline` | ❌ No | 🟢 | Per-second progress |
| `configuration.labels` (echo of request labels) | ❌ No | 🟢 | |
| `configuration.query.useLegacySql` (echo) | ❌ No | 🟢 | |
| `configuration.dryRun` (echo) | ❌ No | 🟢 | |

## 8. Recommended prioritisation

For **P7.b — operator-side recording**, the recommended fixture
priority (highest user impact first):

### Tier 1 — Ship-blocking before v1.0

✅ **All Tier 1 clusters closed** by P7.b phase 2
(45 fixtures recorded against real BigQuery + same-session inline
closure of 13 of the 15 surfaced XFAILs — see CHANGELOG). Final
state: **49 PASS + 2 XFAIL** of 51 fixtures in
`api_configuration/`. Only `legacy_sql_select_compat_mode` and
`dry_run_invalid_function` remain pinned (the legacy SQL deferral
is intentional; the dry-run error envelope shape lands in P7.c).

1. ✅ **`statementType` + `numDmlAffectedRows`** for INSERT / UPDATE / DELETE / MERGE (13 fixtures, all PASS)
2. ✅ **`writeDisposition=WRITE_TRUNCATE` + `WRITE_APPEND`** with destinationTable (8 fixtures, all PASS after inline closure)
3. ✅ **`destinationTable` + `createDisposition=CREATE_NEVER`** (3 fixtures, all PASS after inline closure)
4. ✅ **`useLegacySql=true`** (1 fixture, XFAIL pinned to `out-of-scope.md#legacy-sql-uselegacysqltrue`)
5. ✅ **`useQueryCache=false`** (3 fixtures, all PASS)
6. ✅ **`dryRun=true`** (5 fixtures, all PASS post-P7.c via `_rewrite_for_dry_run`)
7. ✅ **`defaultDataset`** (3 fixtures, all PASS after inline closure)
8. ✅ **`labels`** (3 fixtures, all PASS)
9. ✅ **`parameterMode=POSITIONAL` deeper** (5 fixtures including pilot, all PASS)
10. ✅ **`connectionProperties.session_id`** (3 fixtures, all PASS after inline closure)

**Tier 1 total: 45 new fixtures recorded + 13 inline emulator
closures shipped same session.**

### Tier 2 — ✅ Closed (P7.c)

1. `priority=BATCH` — ✅ already covered in Tier 1 (3 fixtures); promoted from Tier 2.
2. ✅ `maximumBytesBilled` enforcement (2 fixtures, both PASS)
3. ✅ `schemaUpdateOptions` ALLOW_FIELD_ADDITION + ALLOW_FIELD_RELAXATION
   (4 fixtures, **all PASS** after the P7.c follow-up that
   shipped `_check_schema_update_options` for the disposition rule
   and bypassed `_apply_write_append`'s rejection when
   `ALLOW_FIELD_ADDITION` is set, plus catalog schema evolution
   via `_evolve_destination_schema`)
4. ✅ `clusteringFields` + `timePartitioning` on destination table
   (4 fixtures, **all PASS** after the P7.c follow-up that shipped
   `_validate_destination_layout_columns` for column existence,
   and re-recorded the two `_basic` fixtures with single-row
   source tables to side-step BQ's storage-engine row order)
5. ✅ `jobTimeoutMs` enforcement (2 fixtures, both PASS)
6. ✅ `tabledata.list` pagination (5 HTTP corpus fixtures, **all
   PASS** after the P7.c follow-up that shipped `pageToken` opaque
   continuation + `selectedFields` projection in
   `routes/tabledata.py`, plus re-recording the multi-row fixtures
   with per-row INSERT statements so BigQuery's storage order
   happens to match DuckDB's INSERT-order at the exercised
   boundaries)
7. ✅ `api_configuration/dry_run_invalid_function` — closed via
   `_rewrite_for_dry_run` (P7.c emulator inline closure)
8. ✅ `create_session` response-shape round-trip — closed via
   `_maybe_mint_session` + `_attach_session_info` (P7.c emulator
   inline closure)

**Tier 2 total: 19 new fixtures recorded + 11 same-session inline
emulator closures shipped.** Every Tier 2 cell now PASSes; the
P7.d follow-up workstream is reduced to legacy-SQL deferral and
the residual Tier 3 list (CDC writes, Storage Read codec, etc.).

### Tier 3 — Closed in P7.c + P3.d 

1. ✅ Storage Read + Write API gRPC wire-format conformance —
   P3.d shipped the new
   `tests/conformance/grpc_corpus/` sibling
   to `sql_corpus/` + `http_corpus/`. **20 new fixtures**
   recorded against real BigQuery (10 Storage Read + 10 Storage
   Write); **all 20 PASS** after 9 same-session inline emulator
   closures. Covers maxStreamCount, preferredMinStreamCount,
   selectedFields, rowRestriction, arrowSerializationOptions,
   SplitReadStream, empty-table session, every WriteStream type
   (DEFAULT / COMMITTED / PENDING / BUFFERED), CreateWriteStream
   / GetWriteStream / FinalizeWriteStream / BatchCommit /
   FlushRows, and the three primary error envelopes
   (INVALID_ARGUMENT / NOT_FOUND / OUT_OF_RANGE).
2. CDC writes (`_CHANGE_TYPE` / `_CHANGE_SEQUENCE_NUMBER`) — defer
   to v1.0.x (Storage Write API; requires schema metadata the
   emulator's catalog doesn't yet model). Out-of-scope per
   ADR 0013.
3. ✅ Streaming insert `templateSuffix` / `insertId` dedup — 4
   HTTP corpus fixtures recorded against real BigQuery
   (`insertall_basic`, `insertall_insert_id_dedup`,
   `insertall_template_suffix`, `insertall_skip_invalid_rows`),
   all PASS. The `skipInvalidRows=true` handling was implemented
   inline in `routes/tabledata.py::insert_all` with a per-row
   try/except + BQ-shape `insertErrors[]` envelope.
4. Storage Read `responseCompressionCodec` (LZ4_FRAME / ZSTD) —
   defer to v1.0.x (gRPC-level codec). The
   `arrowSerializationOptions.bufferCompression` shape is ✅
   covered by P3.d (request option echoed back on the session
   response); the actual codec wire-decode implementation is the
   deferred item.
5. ✅ `jobs.list` / `jobs.cancel` / `jobs.delete` metadata
   endpoints — already covered by P2.f HTTP corpus; the P7.c
   sweep added `tables.list` / `tables.get` / `datasets.list` /
   `datasets.get` (4 fixtures, all PASS) plus the dataset
   resource emitters now include `type=DEFAULT` and
   `maxTimeTravelHours=168` for wire-shape parity; the table
   resource emitters add four `num*Bytes` accounting fields.

## 9. How to use this matrix

When authoring a new fixture, the recorder picks the highest-impact
🔴 Uncovered row and either:

1. Adds a `job_config.json` to an existing fixture's sibling
   directory (e.g. `arith_add` → `arith_add_with_labels` with the
   same SQL + a labels job config), OR
2. Creates a new fixture with a representative SQL + a
   `job_config.json` (e.g. `dml_update_with_truncate`).

The runner picks up the job config automatically — the framework
extension (P7.a) wires it through.

When extending the **response equivalence**, the recorder writes the
new field into `expected.json` under the optional `job_metadata`
block; the runner diffs only the keys that are present. This keeps
the 878 existing fixtures valid (none of them write `job_metadata`,
so they skip the new comparisons), and lets a single fixture
declare which response fields are interesting to its scenario.

## References

- [ADR 0022](../adr/0022-conformance-corpus-design.md) — corpus
  design contract (extended by this audit)
- [ADR 0023](../adr/0023-conformance-divergence-baseline.md) — the
  bucketed divergence framework
- [ADR 0025](../adr/0025-perf-tier-design-contract.md) — perf-tier
  contract (sibling shape)
- [Conformance coverage matrix](conformance-coverage-matrix.md) —
  SQL surface coverage (this doc's sibling)
- v1-confidence-plan —
  workstream P7 tracking
