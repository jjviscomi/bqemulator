# Changelog

All notable changes to this project are documented in this file. The
format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and [Common Changelog](https://common-changelog.org/); the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Sections appear in the order **Changed / Added / Removed / Fixed**,
with optional **Deprecated** and **Security** sections when a release
has such entries. Each bullet is a single imperative-mood sentence.

Entries are authored at release time, not on every PR. The release
operator reads `git log <prev-tag>..HEAD` and synthesises one bullet
per user-visible change. There is no `## [Unreleased]` section
between releases — the first heading below is always the most recent
shipped version.

Released sections are immutable except to fix an error (typo, wrong
reference, factual mistake about what shipped). Adding context,
expanding explanations, or back-filling forgotten work goes in a
new release.

See [`docs/architecture/contributing/documentation-style-guide.md`](docs/architecture/contributing/documentation-style-guide.md)
for the full entry-form rules and worked examples.

## [1.1.3] - 2026-06-03

### Fixed

- Return BigQuery's per-type result shape for single-statement DDL submitted via `jobs.query` — the analyzed schema with zero rows for `CREATE TABLE` / CTAS / `CREATE VIEW`, an empty result for `ALTER` / `CREATE SCHEMA` / `DROP`, DML-style counts for `TRUNCATE` — instead of DuckDB's status column.
- Report the correct `statementType` (`CREATE_FUNCTION` / `CREATE_TABLE_FUNCTION`) for single `CREATE FUNCTION` / `CREATE TABLE FUNCTION` statements instead of `SCRIPT`, and execute `DROP FUNCTION` / `DROP PROCEDURE` / `DROP TABLE FUNCTION` submitted via `jobs.query`.
- Reject `DROP SCHEMA` on a non-empty dataset with a `resourceInUse` error, matching BigQuery; only `DROP SCHEMA ... CASCADE` removes the dataset's contents.
- Return the final statement's result from a multi-statement script, rather than the last row-producing statement's.
- Register datasets created by `CREATE SCHEMA` inside multi-statement scripts in the catalog so later statements and `INFORMATION_SCHEMA` resolve them.
- Remove dropped tables, views, and datasets from the catalog when dropped via `jobs.query`.
- Cap `grpcio-tools` and `grpcio-health-checking` below 1.81 to keep the generated gRPC stubs building against the example-pinned `grpcio` 1.80.0.

### Security

- Raise the PyJWT floor to `>=2.13.0` to close PYSEC-2025-183 (transitive via `google-auth`; the emulator does not exercise the JWT codepath at runtime).

## [1.1.2] - 2026-05-31

### Changed

- Refactor about sixty internal functions across SQL / scripting / jobs / streaming / catalog / versioning modules below cyclomatic complexity rank C with no observable runtime behaviour change.
- Tighten the cyclomatic-complexity gate's `--max-absolute` and `--max-modules` thresholds from rank C to rank B ([ADR 0041](docs/adr/0041-complexity-ratchet-to-b.md) + [ADR 0042](docs/adr/0042-module-ceiling-ratchet-to-b.md)).
- Pin `starlette<1.0` to keep the `httpx`-backed `TestClient` working against the starlette 0.x line.
- Bump GitHub Actions: `actions/checkout` 4.3.1 → 6.0.2, `actions/cache` 4.3.0 → 5.0.5, `docker/setup-buildx-action` 3.12.0 → 4.1.0, `docker/setup-qemu-action` 3.7.0 → 4.1.0, `crate-ci/typos` 1.46.3 → 1.47.0.

### Fixed

- Emit the un-padded year for `FORMAT_DATE('%Y', d)` on dates before year 1000, matching BigQuery's documented behaviour.

## [1.1.1] - 2026-05-27

### Fixed

- Persist and enforce `CREATE ROW ACCESS POLICY` DDL submitted via `jobs.query` / `jobs.insert`, including the `IF NOT EXISTS`, grantee-less (filter-only), and backtick-quoted forms that previously failed to register.
- Register datasets and tables created through SQL DDL (`CREATE SCHEMA`, `CREATE TABLE`) in the catalog so `INFORMATION_SCHEMA`, `tables.list`, and row-access-policy target validation resolve them.

### Security

- Exclude `fastapi` 0.136.3 (MAL-2026-4750), a tampered release that injects an undocumented `fastar` dependency.

## [1.1.0] - 2026-05-24

### Changed

- Promote the cyclomatic-complexity gate from non-blocking rank E to required rank C; refactor ten functions to comply.
- Switch the release flow from per-PR `Unreleased` accumulation to release-time CHANGELOG authoring.
- Rewrite `scripts/changelog.py` from `Unreleased` promotion into an operator-authored section validator and date stamper.
- Sweep docstrings and inline comments across `src/bqemulator/**` to comply with the new documentation style guide.
- Sweep reference and architecture documentation to comply with the new documentation style guide.

### Added

- Resolve `SESSION_USER()` as a SQL function with caller-aware substitution for row-access-policy filters.
- Recognise `CURRENT_USER()` and `@@session.user` as aliases for `SESSION_USER()`.
- Thread the caller identity into Storage Read `row_restriction` filters so RAP-via-`SESSION_USER` row filters honour the `X-Bqemu-Caller` gRPC header.
- Expose `INFORMATION_SCHEMA.SCHEMATA`, `TABLES`, `COLUMNS`, `TABLE_OPTIONS`, `VIEWS`, and `PARTITIONS` as virtual views in the catalog rewriter.
- Ship 11 TPC-DS conformance fixtures (q12, q20, q37, q45, q57, q65, q79, q81, q82, q93, q98).
- Ship 18 `INFORMATION_SCHEMA` conformance fixtures recorded against real BigQuery.
- Publish OpenSSF Scorecard reports + a public badge in the README.
- Sign GitHub Release artefacts with SLSA v1.0 Build Provenance attestations.
- Add a non-blocking code-quality umbrella (`radon` / `xenon` complexity, `jscpd` duplication, `vulture` dead-code) under `make quality`.
- Add a documentation style guide at `docs/architecture/contributing/documentation-style-guide.md` covering docstrings, code comments, reference docs, and the changelog.
- Automate the README shields.io badge cache-bust suffix on every version bump.

### Fixed

- Emit the closing backtick in `INFORMATION_SCHEMA` rewriter patterns so backtick-quoted references tokenise cleanly downstream.
- Type the empty-rows `(VALUES (NULL, ...))` form in `INFORMATION_SCHEMA` virtual views via `CAST(NULL AS <type>)` so schema introspection returns BigQuery-documented column types instead of `INTEGER`.
- Round-trip DDL `NOT NULL` constraints from `CREATE TABLE` into `TableFieldSchema.mode` so `INFORMATION_SCHEMA.COLUMNS.is_nullable` reports the authored mode.
- Extract `PARTITION BY`, `description`, `require_partition_filter`, and `partition_expiration_days` from `CREATE TABLE` DDL into `TableMeta` so `INFORMATION_SCHEMA.TABLE_OPTIONS` projects from them.
- Render `STRUCT` and `ARRAY` column types correctly in `INFORMATION_SCHEMA.COLUMNS` (previously flattened to `STRING`).
- Resolve repo-self-reference links in lychee link-checking via `--remap` so docs that reference files added in the same PR no longer 404 against `main`.

### Security

- Scope every workflow's top-level `permissions:` to least-privilege; move write scopes per-job.
- SHA-pin every GitHub Action reference and the `Dockerfile` base-image digest.
- Tighten Python example dependency floors to clear OSV-scanner findings.
- Bump `github.com/docker/docker` to v28.5.2, `golang.org/x/net` to v0.55.0, and the Go toolchain to 1.25.10 in Go example modules.
- Bump `@google-cloud/bigquery` to v8, `@google-cloud/bigquery-storage` to v5, and `google-auth-library` to v10 in Node example/test projects; the modern chain no longer transitively depends on `uuid` v9.
- Bump `testcontainers-go` to v0.42.0 (uses `github.com/moby/moby/client` rather than removed `docker/docker/api/types`).
- Pin the example `python:3.11-slim` and `node:20-slim` images by digest; hash-pin `pip install --require-hashes` in the compose example; switch the cloud-run-local example to `npm ci`.
- Add `osv-scanner.toml` documenting five unpatched `docker/docker` advisories (transitive of `testcontainers-go` in example code only; no upstream fix available).

## [1.0.2] - 2026-05-23

### Fixed

- Run the scio example pipeline end-to-end against bqemulator via a `fake-gcs-server` sidecar and an `EmulatorBigQueryServices` adapter for Beam's `BigQueryIO.Write.withTestServices`.

## [1.0.1] - 2026-05-23

### Added

- **INFORMATION_SCHEMA conformance corpus — 18 fixtures recorded
  + two G4 rewriter bug fixes**. The G4 rewriter implementation
  (2026-05-21 work) covering ``SCHEMATA`` / ``TABLES`` /
  ``COLUMNS`` / ``TABLE_OPTIONS`` / ``VIEWS`` / ``PARTITIONS``
  shipped with the fixture *queries* but no recorded baselines;
  this PR records all 18 against real BigQuery, exposing two
  pre-existing rewriter bugs that are now closed:

  - **Stray trailing backtick** — every ``_build_patterns``
    regex matched a backtick-quoted reference like
    ``` `dataset.INFORMATION_SCHEMA.TABLES` ``` but didn't
    consume the closing ``` ` ```, leaving a stray backtick in
    the rewritten SQL that broke the downstream SQLGlot
    tokeniser. Added ``` `? ``` after each ``{view}`` pattern.
  - **Bare-`NULL` columns in the empty-rows path** — when the
    matched view's catalog state was empty, the rewriter
    emitted ``(VALUES (NULL, NULL, …, NULL) … WHERE FALSE)``.
    DuckDB inferred every NULL column as ``INTEGER``, so the
    wire schema showed ``schema_name: INTEGER`` etc. instead
    of the BigQuery-documented types. Refactored each of the
    six empty-row helpers to emit
    ``CAST(NULL AS STRING) AS catalog_name, CAST(NULL AS TIMESTAMP) AS creation_time, …``
    with a per-view ``_<VIEW>_COLUMN_TYPES`` tuple driving the
    types (STRING / TIMESTAMP / INTEGER per BigQuery's docs).

  **Third bug fix landed in the same PR** — DDL ``NOT NULL``
  constraints now round-trip into ``TableFieldSchema.mode``. The
  pre-fix ``ddl_sync._introspect_schema`` hard-coded every column
  to ``mode="NULLABLE"`` regardless of the DDL because DuckDB's
  Arrow exporter always emits ``nullable=True``. The fix
  cross-references DuckDB's ``PRAGMA table_info`` (which
  preserves the ``notnull`` flag) and routes that into the
  ``REQUIRED``/``NULLABLE`` mode the BigQuery
  INFORMATION_SCHEMA.COLUMNS ``is_nullable`` column reads from.
  The ``api/routes/jobs.py:_schema_from_create_table`` preview
  helper also now reads the SQLGlot ``NotNullColumnConstraint``
  AST so the REST job-preview path is consistent.

  **Fourth fix in the same PR** — ``CREATE TABLE`` DDL extras
  now flow into ``TableMeta``. A new ``_extract_ddl_metadata``
  helper in ``ddl_sync`` parses the SQLGlot ``Create`` AST and
  populates:

  - ``time_partitioning.field`` from ``PARTITION BY <col>``
    (``_PARTITIONDATE`` / ``_PARTITIONTIME`` pseudo-columns are
    excluded per BigQuery's ingestion-time contract where
    ``field`` is None).
  - ``description`` from ``OPTIONS(description="…")``.
  - ``time_partitioning.require_partition_filter`` from
    ``OPTIONS(require_partition_filter=TRUE)``.
  - ``time_partitioning.expiration_ms`` from
    ``OPTIONS(partition_expiration_days=N)``.

  The TABLE_OPTIONS rewriter was already structured to project
  from these ``TableMeta`` fields — populating them at DDL-sync
  time closes the TABLE_OPTIONS fixtures end-to-end.

  **Fifth fix in the same PR** — STRUCT / ARRAY column types now
  render correctly in ``INFORMATION_SCHEMA.COLUMNS``. The pre-fix
  ``_introspect_schema`` mapped every Arrow type through the flat
  ``arrow_type_to_bq_type_name`` helper, so a ``STRUCT<city
  STRING, zip INT64>`` column landed as ``data_type='STRING'`` and
  an ``ARRAY<STRING>`` as ``data_type='STRING'`` too. A new
  recursive ``_arrow_field_to_table_field`` helper handles
  Arrow lists (→ BigQuery REPEATED mode), structs (→ ``RECORD``
  type with nested ``TableFieldSchema`` tuple), and scalars
  uniformly. The existing ``_render_data_type`` /
  ``_render_inner_type`` functions in the COLUMNS emitter then
  format ``STRUCT<...>`` / ``ARRAY<...>`` correctly from the
  populated schema.

  Fixture state: **18 / 18 PASS** (every G4 INFORMATION_SCHEMA
  fixture green).

  - **SCHEMATA** (3 / 3), **TABLES** (3 / 3),
    **COLUMNS** (3 / 3), **TABLE_OPTIONS** (3 / 3),
    **VIEWS** (3 / 3), **PARTITIONS** (3 / 3)

  Coverage-matrix regenerated: corpus fixture count grows by 18
  (1141 → 1159); the INFORMATION_SCHEMA category lifts from
  **0 / 6 covered** to **6 / 6 covered** (all green, no XFAILs).
  Recording cost: ~$0 (18 queries × 10 MiB minimum scan).

### Changed

- Bump `testcontainers` in the scio example for Docker 27+ compatibility.

### Fixed

- Emit a bare record-batch IPC message from the Storage Read `ReadRows` handler instead of a full Arrow IPC stream, so `reader.to_arrow(session)` works against the standard `google-cloud-bigquery-storage` client.

## [1.0.0] - 2026-05-22

### Added

- Expose REST API parity for Datasets, Tables, Jobs, TableData, Routines, and Models, including multipart and resumable upload endpoints for `load_table_from_file` workflows.
- Expose `INFORMATION_SCHEMA` views: `TABLES`, `COLUMNS`, `ROUTINES`, `VIEWS`, `MATERIALIZED_VIEWS`, `PARTITIONS`, and `TABLE_OPTIONS`.
- Expose the Storage Read API as a gRPC servicer with both Arrow and Avro wire formats.
- Expose the Storage Write API as a gRPC servicer with `DEFAULT`, `COMMITTED`, `PENDING`, and `BUFFERED` stream types and both proto and Arrow row payload formats.
- Translate the BigQuery GoogleSQL dialect to DuckDB SQL via SQLGlot, with rules covering date/time/timestamp/interval, string, array, struct, range, geography, statistical aggregates, approximate aggregates, JSON, regular expressions, bit operations, and civil-time helpers.
- Interpret BigQuery scripting: `DECLARE`, `SET`, `BEGIN`/`END`, `IF`, `WHILE`, `FOR`, `LOOP`, `BREAK`, `CONTINUE`, `RETURN`, `RAISE`, `EXCEPTION WHEN ERROR THEN`, and `BEGIN`/`COMMIT`/`ROLLBACK TRANSACTION`.
- Execute SQL UDFs, table-valued functions, and JavaScript UDFs via embedded V8 (optional `bqemulator[udf-js]` extra).
- Support time travel (`FOR SYSTEM_TIME AS OF`), table snapshots, table clones, and materialized views with `BQ.REFRESH_MATERIALIZED_VIEW` dispatch.
- Enforce row-access policies and authorized views with caller-identity propagation.
- Support `GEOGRAPHY` (planar via DuckDB-spatial with S2-sphere helpers for distance, length, area, perimeter, and DWithin), `RANGE<DATE>` / `RANGE<DATETIME>` / `RANGE<TIMESTAMP>`, `INTERVAL`, and `NUMERIC` / `BIGNUMERIC` arithmetic.
- Load tables from CSV, JSON, Avro, ORC, and Parquet; extract tables to CSV, JSON, Avro, and Parquet.
- Publish a multi-arch Docker image (`linux/amd64` + `linux/arm64`) signed with keyless cosign via GitHub OIDC.
- Register a native pytest plugin: `pip install bqemulator` exposes the `bqemu_server` fixture, which starts an ephemeral in-process emulator on random free ports and sets `BIGQUERY_EMULATOR_HOST`.
- Exercise the live container against the Python, Node.js, Go, and Java BigQuery client libraries plus the `bq` CLI on every release.
- Ship a conformance corpus of 1,200+ fixtures recorded against real BigQuery covering SQL semantics, REST wire format, and gRPC Storage R/W; documented divergences are pinned in `tests/conformance/divergences.py`.
- Emit `structlog` JSON logs, OpenTelemetry traces over a configurable OTLP exporter, and Prometheus metrics on an HTTP endpoint.
- Clone a real BigQuery project's schema (and optionally data) into a local emulator data directory via `bqemulator import --from-project`; back up and restore via `bqemulator backup` / `bqemulator restore`.
- Automate the release pipeline via `scripts/bump_version.py`, `scripts/changelog.py`, and `scripts/release.py`, with `make release-dry-run` and `make release` entry points.
- Ship 14 runnable example projects under `docs/examples/` covering Python (pytest-integration, dbt-local, airflow-dag-test, pyspark-bigquery), Node.js (NestJS app, Cloud Run local), Go (Beam pipeline, Dataflow-style ETL), Java/Scala (Spring Boot, Spotify Scio), docker-compose full-stack, and CI recipes (GitHub Actions, GitLab CI, CircleCI).
- Publish an mkdocs-Material documentation site with getting-started, per-language quickstarts, topic guides, architecture pages, and 32 architecture decision records.
- Generate the compatibility matrix, conformance coverage matrix, SQL function mapping, and API coverage reference docs from source, with `make <name>-check` drift gates wired into `make verify` and the per-PR CI job.

### Security

- Reject path traversal in resumable-upload identifiers via the `^[A-Za-z0-9_-]{8,64}$` regex on `upload_id`.
- Enforce the configured size cap before disk write to prevent unbounded resource consumption.
- Parse multipart envelopes via stdlib `email.parser` with a media-part type allowlist, preventing envelope injection.
- Publish to PyPI via Trusted Publishing (no long-lived tokens); wheels carry sigstore attestations.
- Sign GHCR images keylessly via cosign with the GitHub OIDC certificate identity.
