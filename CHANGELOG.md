# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entry types:

- **Added** — new features
- **Changed** — changes to existing behavior
- **Deprecated** — features slated for removal (with removal timeline)
- **Removed** — features that have been removed (must have been deprecated
  for at least 2 MINOR versions or 6 months)
- **Fixed** — bug fixes
- **Security** — security fixes or relevant notices

Each PR that changes observable behavior MUST add an entry under `Unreleased`.
On release, `scripts/changelog.py X.Y.Z` moves those entries into a new version
section and adds the release date.

## [Unreleased]

### Changed

- **scio example: testcontainers bump + #17 investigation notes.**
  Bumped ``testcontainers`` 1.19.7 → 1.20.4 in the scio example's
  ``build.sbt`` — the older docker-java 1.32 client doesn't talk to
  Docker 27+ (modern Docker Desktop returns ``client version 1.32 is
  too old``). The end-to-end Beam BigQueryIO routing attempted under
  issue #17 turned out to be deeper than a single flag/env-var fix
  — ``--bigQueryEndpoint`` does override the Apiary ``Bigquery``
  client's ``rootUrl``, but Beam's ``BigQueryIO.Write`` defaults to
  the ``BATCH_LOADS`` method which stages rows to GCS before
  invoking a BigQuery LOAD job (no GCS-compatible shim in the
  emulator), and Beam's auth refresh fires before the redirected
  HTTP call so ``OAuth2Credentials.refresh()`` 400s against
  ``oauth2.googleapis.com`` even with
  ``--gcpCredentialFactoryClass=NoopCredentialFactory``. The
  ``CustomersPipelineSpec`` stays at the wiring-only smoke for
  v1.0.1; the full set of constraints is captured in the spec's
  header comment and tracked on issue #17 for v1.0.2+.

## [1.0.0] — 2026-05-22

### Added

- **REST API parity** — Datasets, Tables, Jobs, TableData, Routines, Models,
  with multipart and resumable upload endpoints for `load_table_from_file`
  workflows. `INFORMATION_SCHEMA` views (`TABLES`, `COLUMNS`, `ROUTINES`,
  `VIEWS`, `JOBS`, `JOBS_BY_*`, `MATERIALIZED_VIEWS`, `PARTITIONS`,
  `TABLE_OPTIONS`, etc.) queryable via the standard SQL path.
- **Storage Read API** — gRPC servicer with both Arrow and Avro wire formats.
  Avro is the Java client's default; both clients (Python `fastavro`, Node
  `avsc`, Go `linkedin/goavro`, Java canonical Apache Avro) interoperate
  against the same recorded fixtures.
- **Storage Write API** — gRPC servicer with all four stream types (`DEFAULT`,
  `COMMITTED`, `PENDING`, `BUFFERED`), both proto and Arrow row payload
  formats, `FinalizeWriteStream` / `BatchCommitWriteStreams` /
  `FlushRows` / `GetWriteStream`.
- **GoogleSQL translator** — SQLGlot-backed transpiler from BigQuery dialect
  to DuckDB SQL, with a rule registry covering the GoogleSQL function surface
  (date / time / timestamp / interval, string, array, struct, range,
  geography, statistical aggregates, approximate aggregates, JSON,
  regular expressions, bit ops, civil-time helpers, and more).
- **BigQuery scripting** — interpreter for `DECLARE` / `SET` / `BEGIN` …
  `END` / `IF` / `WHILE` / `FOR` / `LOOP` / `BREAK` / `CONTINUE` /
  `RETURN` / `RAISE` / `EXCEPTION WHEN ERROR THEN`, plus a
  `BEGIN TRANSACTION` / `COMMIT TRANSACTION` / `ROLLBACK TRANSACTION`
  shim.
- **User-defined functions** — SQL UDFs, table-valued functions (TVFs), and
  JavaScript UDFs via embedded V8 (optional `bqemulator[udf-js]` extra).
- **Versioning surface** — time travel (`FOR SYSTEM_TIME AS OF`), table
  snapshots, table clones, and materialized views with
  `BQ.REFRESH_MATERIALIZED_VIEW` dispatch.
- **Authorization surface** — authorized views (with RAP propagation) and
  row-access policies with caller-identity enforcement.
- **Specialized types** — `GEOGRAPHY` (planar via DuckDB-spatial with
  S2-sphere helpers for distance / length / area / perimeter / DWithin),
  `RANGE<DATE>` / `RANGE<DATETIME>` / `RANGE<TIMESTAMP>`, `INTERVAL`,
  `NUMERIC` / `BIGNUMERIC` arithmetic, civil-time helpers.
- **Load / extract formats** — load supports CSV, JSON, Avro, ORC, and
  Parquet. Extract supports CSV, JSON, Avro, and Parquet. (ORC extract is
  intentionally out of scope — see `docs/reference/out-of-scope.md`.)
- **Multi-arch Docker image** — `ghcr.io/jjviscomi/bqemulator` builds for
  `linux/amd64` + `linux/arm64`, with cosign keyless signatures via GitHub
  OIDC.
- **Native pytest plugin** — `pip install bqemulator` registers a pytest
  plugin; the `bqemu_server` fixture starts an ephemeral in-process emulator
  on random free ports, sets `BIGQUERY_EMULATOR_HOST`, and tears down on
  exit.
- **Five-client E2E** — every release exercises the live container against
  the official Python, Node.js, Go, and Java BigQuery client libraries plus
  Google's `bq` CLI.
- **Conformance corpus** — 1,200+ fixtures recorded against real BigQuery
  covering SQL semantics, REST wire format, and gRPC Storage R/W. Drift
  between the emulator and BigQuery surfaces as test failures; documented
  divergences are pinned in `tests/conformance/divergences.py` with ADR
  references.
- **Observability** — `structlog` JSON logs, OpenTelemetry tracing
  (configurable OTLP exporter), Prometheus metrics endpoint.
- **Admin surface** — `bqemulator import --from-project` clones a real
  BigQuery project's schema (and optionally data) into a local emulator
  data directory; backup / restore via `bqemulator admin backup` and
  `bqemulator admin restore`.
- **Release tooling** — `scripts/bump_version.py`, `scripts/changelog.py`,
  and `scripts/release.py` automate the version bump → changelog finalise
  → release commit + annotated tag flow. `make release-dry-run` previews;
  `make release` applies.
- **Example projects (14)** — `docs/examples/` ships runnable example
  projects for every supported language + framework + deployment pattern:
  Python (pytest-integration, dbt-local, airflow-dag-test,
  pyspark-bigquery), Node.js (NestJS app, Cloud Run local), Go (Beam
  pipeline, Dataflow-style ETL), Java/Scala (Spring Boot,
  Spotify Scio), docker-compose full-stack (app + emulator + Prometheus +
  Grafana), and CI recipes (GitHub Actions, GitLab CI, CircleCI). Each
  has its own `make test`; all are validated in CI by the new
  `.github/workflows/examples.yml` workflow (intentionally non-blocking
  on main so an upstream framework regression cannot stall emulator PRs).

### Documentation

- **mkdocs-Material site** — getting-started + per-language quickstarts +
  topic guides (loading data, querying, query parameters, streaming
  inserts, storage API, routines + UDFs, scripting, time travel,
  materialized views, row access policies, authorized views,
  `INFORMATION_SCHEMA`, GEOGRAPHY, RANGE, INTERVAL, admin endpoints, backup
  and restore, CI/CD patterns, dbt, Airflow, Spark, the `bq` CLI,
  observability).
- **Auto-generated reference docs** — compatibility matrix, conformance
  coverage matrix, SQL function mapping, and API coverage. Each ships
  with a `make <name>-check` drift gate wired into `make verify` and
  the per-PR `Docs-drift gates` CI job, so a regenerated doc can't
  drift from the live source between commits. A fifth audit doc —
  `docs/reference/api-configuration-coverage-matrix.md` — is the
  manually-maintained sibling that tracks the *configuration knob*
  surface (the part that can't be mechanically derived from the
  route handlers); it's labelled "Audit dated" at the top of the
  file and refreshed during the pre-release doc sweep.
- **Architecture Decision Records** — 32 ADRs documenting non-obvious
  design choices (DuckDB vs. alternatives, hexagonal architecture, scripting
  execution model, materialized-view refresh semantics, caller identity
  and row-access enforcement, conformance corpus design, divergence
  baseline, perf / chaos / mutation / fuzz / differential tier contracts,
  upload host endpoints, `bq` CLI as a fifth conformance client).

### Testing

- **7-tier test pyramid** — unit (hermetic), property (Hypothesis),
  integration (in-process + client), conformance (compared to real
  BigQuery), e2e (live container × five clients), performance
  (`pytest-benchmark` with `--benchmark-compare-fail=median:10%`), chaos
  (deliberately disruptive — concurrency, resource exhaustion, crash
  recovery, storage failures, network failures). Sibling tiers: differential
  (row-order perturbation of the conformance corpus), mutation (`mutmut`
  pilot on pure-domain modules), fuzz (Atheris on the SQL translator,
  dynamic-protobuf decoder, and Arrow bridge).
- **Coverage gate** — combined unit + property + integration coverage
  must reach 90% line + branch in CI.

### Security

- **Path-traversal hardening** on the resumable-upload `upload_id` regex
  (`^[A-Za-z0-9_-]{8,64}$`); size cap enforced before disk write to
  prevent unbounded resource consumption; multipart envelope injection
  prevented via stdlib `email.parser` plus a media-part type allowlist.
- **PyPI publish via Trusted Publishing** (no long-lived tokens); wheels
  carry sigstore attestations.
- **GHCR image signing** via keyless cosign with GitHub OIDC certificate
  identity.

### Known limitations (deferred to v1.0.1)

These ship as documented caveats on the affected example projects.
None affect the core emulator surface.

- **Storage Read API IPC bytes layout** — bqemulator packs the full
  Arrow IPC stream (schema framing + batches) into
  `ReadRowsResponse.arrow_record_batch.serialized_record_batch`
  rather than a single record-batch IPC message; the schema lives
  separately on `ReadSession.arrow_schema`. The
  `google-cloud-bigquery-storage` client's high-level
  `reader.to_arrow(session)` trips
  `Expected IPC message of type record batch but got schema`. The
  `python/pyspark-bigquery` example iterates raw responses through
  `pa.ipc.open_stream` as a workaround. Tracked in
  [#15](https://github.com/jjviscomi/bqemulator/issues/15).
- **Scio test exercises wiring only** — Beam Java BigQueryIO does
  not honour `--bigQueryEndpoint` for the *write* path (that flag
  only gates internal preflight validators); the Java BQ client
  *does* honour `BIGQUERY_EMULATOR_HOST`, but it must be visible to
  the JVM before the first BQ class loads, which is impossible
  when the testcontainer port is allocated at runtime. The
  `java/scio` example's spec asserts the wiring bqemulator owns
  (container up, REST API reachable, dataset creation works); the
  `CustomersPipeline.run` source itself remains production-ready
  for users running against real BigQuery or a long-lived
  bqemulator with a stable port. Tracked in
  [#17](https://github.com/jjviscomi/bqemulator/issues/17).

