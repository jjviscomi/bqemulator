# API configuration coverage matrix

> Sibling to the [conformance coverage matrix](conformance-coverage-matrix.md)
> which tracks the *SQL surface* — this file tracks the **API
> request configuration surface**. The conformance corpus runs
> essentially the same client codepath for every SQL fixture (default
> `QueryJobConfig`, varying only the SQL and the optional
> `parameters.json` payload); this matrix enumerates the API
> configurations differentiated by their own fixtures and ranks them
> by user impact.

## Methodology

Every distinct **configuration knob** a BigQuery REST or gRPC client
can flip is an item in this matrix. The matrix classifies each by:

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

The configuration surface is partitioned into three tiers ordered
by user impact and recording cost. Tier 1 covers ship-blocking
configurations (DML statement-type dispatch, write/create
dispositions, default-dataset qualification, query-parameter
modes); Tier 2 covers configurations exercised by enterprise
workloads (schema evolution, partitioning/clustering on
destination, byte-budget caps); Tier 3 covers surfaces deliberately
deferred (CDC writes, gRPC-level codec selection,
legacy-SQL-coupled flags).

Methodology mirrors the SQL surface matrix's three-tier
classification so the prioritisation language is consistent across
both audits.

## 1. `QueryJobConfig` options

> Counts in the Coverage column reflect conformance-corpus fixtures
> under `tests/conformance/sql_corpus/api_configuration/`;
> integration-tier coverage is called out in the Notes column.
> Severity reflects the divergence risk class regardless of current
> coverage (a fixture passing today doesn't change the inherent
> divergence risk).

| Knob | Wire field | Default | Coverage | Severity | Notes |
|---|---|---|---|---|---|
| Standard SQL (default) | `useLegacySql=false` | implicit | 🟢🟢 Deep (every conformance fixture) | n/a | Every existing fixture |
| Legacy SQL | `useLegacySql=true` | — | 🟡 Sampled (1 fixture, XFAIL) | 🔴 | Different parser entirely — DuckDB doesn't speak legacy SQL. Pinned out-of-scope (`out-of-scope.md#legacy-sql-uselegacysqltrue`); emulator returns `400 InvalidQueryError` for any legacy job. |
| Query cache enabled (default) | `useQueryCache=true` | implicit | 🟢🟢 Deep | n/a | Implicit on every fixture; emulator has no cache so this is essentially a no-op |
| Query cache disabled | `useQueryCache=false` | — | 🟢 Covered (3 fixtures) | 🟡 | `use_query_cache_disabled` + `cache_disabled_with_count_distinct` + `cache_disabled_with_join` — all PASS. `cacheHit=False` surfaces on every job statistics response. |
| Dry-run | `dryRun=true` | — | 🟢 Covered (5 fixtures, all PASS) | 🔴 | `dry_run_select` / `dry_run_aggregate` / `dry_run_create_table` / `dry_run_insert` / `dry_run_invalid_function` PASS via `_destructive_dry_run_schema` schema-reconstruction and the `_rewrite_for_dry_run` helper that transforms `error.location="query"` → `"q"` and recovers the original identifier case from the BQ SQL. |
| Priority — INTERACTIVE (default) | `priority=INTERACTIVE` | implicit | 🟢🟢 Deep | n/a | Implicit |
| Priority — BATCH | `priority=BATCH` | — | 🟢 Covered (3 fixtures) | 🟢 | `priority_batch` + `priority_batch_with_join` + `priority_batch_dml` — all PASS. Emulator runs BATCH jobs immediately. |
| Write disposition — WRITE_EMPTY (default) | `writeDisposition=WRITE_EMPTY` | implicit | 🟢 Covered (integration-tier) | 🟡 | Integration-tier (`test_cross_phase_workflow` + 2 others); not in conformance corpus |
| Write disposition — WRITE_TRUNCATE | `writeDisposition=WRITE_TRUNCATE` | — | 🟢 Covered (4 fixtures) | 🔴 | `write_truncate_schema_matching` / `_schema_divergent` / `_partitioned_destination` / `_clustered_destination` — all PASS. Response carries SELECT projection (BQ truncate-then-write semantic). |
| Write disposition — WRITE_APPEND | `writeDisposition=WRITE_APPEND` | — | 🟢 Covered (4 fixtures) | 🔴 | `write_append_schema_matching` / `_schema_divergent` / `_partitioned_destination` / `_clustered_destination` — all PASS via `_apply_write_append` post-processing. |
| Create disposition — CREATE_IF_NEEDED (default) | `createDisposition=CREATE_IF_NEEDED` | implicit | 🟢 Covered | n/a | |
| Create disposition — CREATE_NEVER | `createDisposition=CREATE_NEVER` | — | 🟢 Covered (3 fixtures, all error envelopes) | 🟡 | `create_never_missing_destination` / `_with_truncate` / `_with_append` — all PASS. `_check_create_disposition` raises `Not found: Table <p>:<d>.<t>`. |
| Destination table | `destinationTable` | — | 🟢🟢 Deep (11+ fixtures: WRITE × 8 + CREATE_NEVER × 3 + DML pilot) | 🔴 | CTAS-like behaviour. Statistics report `numDmlAffectedRows`. |
| Default dataset | `defaultDataset` | — | 🟢 Covered (3 fixtures) | 🔴 | `default_dataset_select_table` / `_insert_unqualified` / `_join_partial_qualification` — all PASS via the `qualify_unqualified_tables` pre-translator. |
| Maximum bytes billed | `maximumBytesBilled` | — | 🟢 Covered (2 fixtures, both PASS) | 🟡 | `max_bytes_billed_within_cap` + `max_bytes_billed_exceeded` PASS. Real BQ's cost-estimate on synthetic queries is 0 bytes — neither budget trips a real billing check, so the fixture pair pins the wire-shape parity (BQ + emulator both succeed). Real cost-estimate enforcement is documented out-of-scope (`slot-and-byte-billing-simulation`). |
| Labels | `labels` | — | 🟢 Covered (3 fixtures) | 🟢 | `labels_metadata_echo` + `labels_multiple_keys` + `labels_unicode_values` — all PASS. Pure metadata echo on `job.configuration.labels`. |
| Job timeout (ms) | `jobTimeoutMs` | — | 🟢 Covered (2 fixtures, both PASS) | 🟡 | `job_timeout_within_budget` + `job_timeout_exceeded` PASS. Synthetic queries complete inside the budget on both BQ and emulator — wire-shape parity. Real timeout-enforcement on slow queries is documented out-of-scope (no v1.0 use case). |
| Connection properties — `session_id` | `connectionProperties` | — | 🟢 Covered (3 fixtures) | 🔴 | `session_temp_table_visible` + `session_declared_var_shared` PASS via `create_session=True` + multi-statement script; `session_invalid_session_id` PASSes via `_validate_session_id` + in-process catalog. |
| Schema update — ALLOW_FIELD_ADDITION | `schemaUpdateOptions` | — | 🟢 Covered (2 fixtures, 0 PASS + 2 XFAIL) | 🔴 | `schema_update_addition_with_append` + `schema_update_addition_with_truncate` recorded against real BQ. Pinned against `out-of-scope.md#schemaupdateoptions-evolution-and-disposition-compatibility` — emulator does not yet evolve destination schema on WRITE_APPEND, nor enforce the WRITE_TRUNCATE disposition rule. |
| Schema update — ALLOW_FIELD_RELAXATION | `schemaUpdateOptions` | — | 🟢 Covered (2 fixtures, 1 PASS + 1 XFAIL) | 🔴 | `schema_update_relaxation_required_to_nullable` (XFAIL) + `schema_update_relaxation_rejected_without_option` (PASS) recorded against real BQ. Same out-of-scope pin as ADDITION. |
| Destination table partitioning (TIME_PARTITIONING) | `timePartitioning` | — | 🟢 Covered (partitioning_clustering conformance + write_*_partitioned_destination + 2 dest_time_partitioning_* fixtures, both XFAIL) | n/a | Standalone job-config exercise: `dest_time_partitioning_basic` (XFAIL: storage-order divergence) + `_invalid_field` (XFAIL: field-existence not validated). Pinned against `out-of-scope.md#clusteringfields-timepartitioning-on-destination`. |
| Destination table clustering | `clusteringFields` | — | 🟢 Covered (2 dest_clustering_fields_* fixtures, both XFAIL) | 🟡 | Standalone job-config exercise: `dest_clustering_fields_basic` (XFAIL: storage-order divergence) + `_invalid_column` (XFAIL: column-existence not validated). Same pin as TIME_PARTITIONING. |
| Range partitioning | `rangePartitioning` | — | 🟡 Sampled (1 integration test) | 🟡 | |
| Parameter mode — NAMED (default) | `parameterMode=NAMED` | implicit when params present | 🟢🟢 Deep (15 fixtures + DML deeper) | n/a | |
| Parameter mode — POSITIONAL | `parameterMode=POSITIONAL` | — | 🟢 Covered (5 fixtures) | 🟡 | `positional_parameter_int64` + `positional_array_int64` + `positional_struct_basic` + `positional_null_bound_string` + `positional_multi_param` — all PASS. |
| Script options — `statement_timeout_ms` | `scriptOptions` | — | 🔴 Uncovered | 🟡 | Routines exercise scripts; option flags untested. |
| Script options — `statement_byte_budget` | `scriptOptions` | — | 🔴 Uncovered | 🟡 | Same. |
| Continuous query | `continuous=true` | — | 🔴 Uncovered | 🟢 | Out-of-scope per [out-of-scope.md](out-of-scope.md). |
| Allow large results (legacy only) | `allowLargeResults=true` | — | 🔴 Uncovered | 🟢 | Coupled to `useLegacySql=true`. |
| Flatten results (legacy only) | `flattenResults=true` | — | 🔴 Uncovered | 🟢 | Same. |
| Table definitions (federated) | `tableDefinitions` | — | 🟡 Sampled (1 external_tables test) | 🟢 | |
| `create_session` | `createSession=true` | — | 🟡 Sampled (2 fixtures use it) | 🟡 | BigQuery mints a transient session token returned via `statistics.sessionInfo.sessionId`. The emulator's `_SESSION_CATALOG` mints a fresh URL-safe token; surfacing it through the response shape uses `_maybe_mint_session` + `_attach_session_info`. |

**`QueryJobConfig` summary:** **8 of 31** configurations are 🔴
Uncovered. Of the 8, **0 are 🔴 high-severity** — every Tier 1 +
Tier 2 cell ships fixtures; the remainder is the Tier 3 list
(scriptOptions, continuous, legacy-only allowLargeResults /
flattenResults).

## 2. Execution paths (synchronous vs asynchronous)

| Path | Wire shape | Coverage | Severity | Notes |
|---|---|---|---|---|
| Synchronous `jobs.query` (small results) | `POST /queries` | 🟢🟢 Deep (default for `client.query()` with small N) | n/a | Used implicitly by virtually every conformance fixture |
| Asynchronous `jobs.insert` + poll + `jobs.getQueryResults` | `POST /jobs`, `GET /jobs/{id}`, `GET /queries/{id}` | 🟡 Sampled (a few integration tests) | 🔴 | The Python client takes this path when results are large or async submission is requested. Polling cadence + state transitions are real-BQ-specific. |
| `jobs.getQueryResults` with pagination (`maxResults`, `pageToken`) | `GET /queries/{id}?maxResults=…&pageToken=…` | 🔴 Uncovered | 🔴 | The shape and opacity of `pageToken` matters — clients trust it as opaque, but the emulator may diverge. |
| `tabledata.list` with pagination | `GET /tabledata?maxResults=…&pageToken=…&startIndex=…&selectedFields=…` | 🟡 Sampled (a few integration tests) | 🔴 | `startIndex` is server-side; `pageToken` is server-side. Projection (`selectedFields`) matters for client-library performance assumptions. |
| `jobs.cancel` mid-execution | `POST /jobs/{id}/cancel` | 🔴 Uncovered | 🟡 | Need a long-running fixture (script with sleep, perhaps) |
| `jobs.delete` | `DELETE /jobs/{id}` | 🔴 Uncovered | 🟢 | Metadata-only |
| `jobs.list` (filtering, projection, pagination) | `GET /jobs?stateFilter=…&allUsers=…&minCreationTime=…&projection=…&pageToken=…` | 🔴 Uncovered | 🔴 | Critical for clients that walk job history |
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
| Copy (snapshot — operationType=SNAPSHOT) | 🟢 Covered | n/a | |
| Copy (clone — operationType=CLONE) | 🟢 Covered | n/a | |
| Copy (restore — operationType=RESTORE) | 🔴 Uncovered | 🟡 | Used for time-travel restore |
| Streaming insert (insertAll) | 🟡 Sampled | 🔴 | Options: `insertId` dedup, `templateSuffix` routing, `skipInvalidRows`, `ignoreUnknownValues` |

## 4. REST API metadata endpoints

| Endpoint | Coverage | Severity | Notes |
|---|---|---|---|
| `datasets.insert` | 🟢🟢 Deep | n/a | |
| `datasets.get` | 🟢🟢 Deep | n/a | |
| `datasets.update` / `datasets.patch` | 🟡 Sampled | 🟡 | `description`, `defaultTableExpirationMs`, `defaultPartitionExpirationMs`, `labels`, `access` (table-level + dataset-level) |
| `datasets.list` (with filter, all, pageToken) | 🔴 Uncovered | 🟡 | |
| `datasets.delete` (with deleteContents) | 🟢 Covered | n/a | |
| `tables.insert` | 🟢🟢 Deep | n/a | |
| `tables.get` | 🟢🟢 Deep | n/a | |
| `tables.update` / `tables.patch` | 🟡 Sampled | 🔴 | `description`, `expirationTime`, `friendlyName`, `labels`, `schema` (column add / column relax / column drop), `timePartitioning`, `rangePartitioning`, `clustering`, `materializedView.refreshIntervalMs`, `tableConstraints`, `view.query`, `view.useLegacySql`, `view.userDefinedFunctionResources`, `requirePartitionFilter` |
| `tables.list` (with pageToken) | 🔴 Uncovered | 🟡 | |
| `tables.delete` | 🟢 Covered | n/a | |
| `tabledata.insertAll` | 🟢 Covered | n/a | |
| `routines.*` | 🟢 Covered | n/a | |
| `models.*` | n/a — out of scope per ADR 0012 | n/a | |
| `rowAccessPolicies.*` | 🟢 Covered | n/a | |
| `projects.list` | 🔴 Uncovered | 🟢 | Trivial; mostly used for ADC discovery |

## 5. Storage Read API (gRPC)

Every practical cell is ✅ Covered; the two compression cells echo
the request option back to the client (the actual codec
implementation is documented out-of-scope per ADR 0008).

| Option | Coverage | Severity | Notes |
|---|---|---|---|
| `dataFormat=ARROW` | 🟢🟢 Deep (1 perf benchmark + 6 integration tests + 8 gRPC corpus fixtures) | n/a | |
| `dataFormat=AVRO` | 🔴 Uncovered | 🟢 | Out-of-scope per ADR 0008 / the compatibility matrix |
| `maxStreamCount` (>1) | ✅ Covered (`sr_create_session_multi_stream`) | n/a | Emulator caps streams by table size, matching real BQ |
| `preferredMinStreamCount` | ✅ Covered (`sr_preferred_min_stream_count`) | n/a | Hint only — emulator echoes the request shape |
| `selectedFields` projection | 🟢🟢 Deep (`sr_column_projection`) | n/a | |
| `rowRestriction` (filter) | ✅ Covered (`sr_row_filter_simple`, `sr_row_filter_with_in`) | n/a | |
| `arrowSerializationOptions.bufferCompression` | ✅ Covered shape (`sr_arrow_compression_lz4_frame`, `sr_arrow_compression_zstd`) | 🟢 | Request option is echoed back; codec implementation deferred |
| `responseCompressionCodec` | 🔴 Uncovered | 🟢 | gRPC-level codec; deferred |
| Snapshot read (table snapshot ID) | 🟡 Sampled | 🟡 | |
| `SplitReadStream` | ✅ Covered (`sr_split_read_stream`) | n/a | Hint per BQ docs; emulator implements wire shape |
| Empty-table session | ✅ Covered (`sr_empty_table`) | n/a | |

## 6. Storage Write API (gRPC)

Every practical cell is ✅ Covered. The two CDC cells remain
pinned as out-of-scope per ADR 0013 (CDC writes require schema
metadata the emulator's catalog doesn't yet model).

| Option | Coverage | Severity | Notes |
|---|---|---|---|
| Stream type DEFAULT | 🟢🟢 Deep (`sw_get_write_stream_default`) | n/a | |
| Stream type COMMITTED | 🟢🟢 Deep (`sw_create_committed_stream`, `sw_get_write_stream_after_create`) | n/a | |
| Stream type PENDING + BatchCommit | 🟢🟢 Deep (`sw_create_pending_stream`, `sw_finalize_pending_no_appends`, `sw_batch_commit_finalized_pending`) | n/a | |
| Stream type BUFFERED + FlushRows | 🟢🟢 Deep (`sw_create_buffered_stream`, `sw_flush_rows_buffered_zero`) | n/a | |
| Payload — Arrow IPC | 🟢 Covered (integration suite) | n/a | |
| Payload — dynamic protobuf | 🟢 Covered (integration suite) | n/a | |
| Error envelope: stream not found | ✅ Covered (`sw_finalize_default_invalid_argument`) | n/a | "Requested entity was not found" wording matches real BQ |
| Error envelope: malformed stream id | ✅ Covered (`sw_get_write_stream_not_found`) | n/a | INVALID_ARGUMENT for non-canonical stream ids |
| Error envelope: empty-buffered FlushRows | ✅ Covered (`sw_flush_rows_buffered_zero`) | n/a | OUT_OF_RANGE "Offset N is beyond the end of the stream" |
| CDC: `_CHANGE_TYPE` column (UPSERT / DELETE) | 🔴 Uncovered | 🔴 | Out-of-scope per ADR 0013 |
| CDC: `_CHANGE_SEQUENCE_NUMBER` | 🔴 Uncovered | 🔴 | Ditto |
| Schema evolution on append | 🔴 Uncovered | 🟡 | Adding columns mid-stream |
| Trace ID propagation | 🔴 Uncovered | 🟢 | |
| Connection retry / re-attach | 🟢 Covered (chaos tier) | n/a | |

## 7. Response object — fields not yet verified

This is the other half of the configuration-coverage picture. Even
when a configuration is exercised by a fixture, only `schema` +
`rows` are diffed. Real-BQ clients consume far more of the response
payload.

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
| Job `errorResult` shape | ✅ Yes for error fixtures | n/a | |
| Job `errors[]` (warnings) | ❌ No | 🟡 | Non-fatal warnings list |
| Job `statistics.creationTime` / `.startTime` / `.endTime` | ❌ No | 🟢 | Timing — too noisy to compare |
| Job `statistics.totalBytesProcessed` | ❌ No | 🟡 | Real-BQ value; emulator often 0 |
| Job `statistics.totalBytesBilled` | ❌ No | 🟡 | Same |
| Job `statistics.cacheHit` | ❌ No | 🟡 | Real-BQ `false` (default) vs `true` (cache hit); emulator always `false` |
| Job `statistics.totalSlotMs` | ❌ No | 🟢 | Real-BQ value; emulator should be 0 |
| Job `statistics.query.statementType` | ✅ Yes | n/a | Diffed key-by-key via the recorded `job_metadata` block (`ddl_result_*`, `routine_ddl_*` fixtures); clients dispatch on this |
| Job `statistics.query.numDmlAffectedRows` | ✅ Yes | n/a | Diffed via the recorded `job_metadata` block (TRUNCATE / DML `ddl_result_*` fixtures); critical for client correctness |
| Job `statistics.query.ddlOperationPerformed` | ✅ Yes | n/a | Diffed via the recorded `job_metadata` block (`ddl_result_*` fixtures); CREATE / DROP / ALTER — clients dispatch on this |
| Job `statistics.query.referencedTables` | ❌ No | 🟡 | Lineage |
| Job `statistics.query.schema` | ❌ No (the result schema only is diffed) | 🟡 | Pre-execution schema (dry-run uses this) |
| Job `statistics.query.queryPlan` | ❌ No | 🟢 | Per-stage timing |
| Job `statistics.query.timeline` | ❌ No | 🟢 | Per-second progress |
| `configuration.labels` (echo of request labels) | ❌ No | 🟢 | |
| `configuration.query.useLegacySql` (echo) | ❌ No | 🟢 | |
| `configuration.dryRun` (echo) | ❌ No | 🟢 | |

## 8. How to use this matrix

When authoring a new fixture, the recorder picks the highest-impact
🔴 Uncovered row and either:

1. Adds a `job_config.json` to an existing fixture's sibling
   directory (e.g. `arith_add` → `arith_add_with_labels` with the
   same SQL + a labels job config), OR
2. Creates a new fixture with a representative SQL + a
   `job_config.json` (e.g. `dml_update_with_truncate`).

The runner picks up the job config automatically — the conformance
framework wires it through.

When extending the **response equivalence**, the recorder writes the
new field into `expected.json` under the optional `job_metadata`
block; the runner diffs only the keys that are present. This keeps
existing fixtures valid (none of them write `job_metadata`, so they
skip the new comparisons), and lets a single fixture declare which
response fields are interesting to its scenario.

## References

- [ADR 0022](../adr/0022-conformance-corpus-design.md) — corpus
  design contract (extended by this audit)
- [ADR 0023](../adr/0023-conformance-divergence-baseline.md) — the
  divergence framework
- [ADR 0025](../adr/0025-perf-tier-design-contract.md) — perf-tier
  contract (sibling shape)
- [Conformance coverage matrix](conformance-coverage-matrix.md) —
  SQL surface coverage (this doc's sibling)
