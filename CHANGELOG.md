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

### Added

- **OpenSSF Scorecard workflow + public badge.** New
  `.github/workflows/scorecard.yml` runs the official
  `ossf/scorecard-action` against `main` on every push, every
  published release, weekly via cron, and on `workflow_dispatch`.
  The score (0–10) publishes to the OSSF public database at
  `https://api.securityscorecards.dev/projects/github.com/jjviscomi/bqemulator`
  (opt-in: `publish_results: true`) and surfaces in the README as a
  badge linking to the public viewer. Scorecard's SARIF output also
  uploads to GitHub's Security tab via
  `github/codeql-action/upload-sarif` so findings show alongside
  CodeQL alerts. Third-party action pinning follows the
  project-wide OpenSSF-alignment rule: SHA pin + trailing
  `# vX.Y.Z` comment (ossf/scorecard-action@4eaacf0 # v2.4.3,
  github/codeql-action/upload-sarif@7211b7c # v4.36.0). Initial
  expected score ~8/10; the two checks that drag are
  CII-Best-Practices (not enrolled) and Contributors (single
  maintainer) — actionable checks (Pinned-Deps, Signed-Releases,
  Branch-Protection, SAST, Code-Review) all score high. The
  public database takes ~24-48h to populate the first score; the
  badge endpoint returns 404 until then. ``scripts/bump_version.py``
  was extended with `_README_SCORECARD_BADGE_RE` so each release
  also rewrites the Scorecard badge URL's ``?v=X.Y.Z`` cache-bust
  suffix (camo TTL ~24h; the bump forces an immediate refresh).
  See [ADR 0037](docs/adr/0037-openssf-scorecard.md) for the
  decision context, expected check breakdown, and trigger
  rationale.

- **Non-blocking code-quality gates: complexity, duplication, dead code.**
  Three concerns that the existing pre-commit chain (ruff + mypy +
  bandit + pip-audit + interrogate + typos) doesn't meaningfully
  enforce today now have observable CI signal:
  - **`radon` + `xenon`** for per-function cyclomatic complexity
    (`make quality-complexity`). Baseline thresholds picked against
    the v1.0.2 codebase — `--max-absolute E --max-modules C --max-average A` —
    so today's code passes; regression past those tiers fails. Fills
    the gap left by ruff's intentionally-suppressed ``C901`` /
    ``PLR0911`` / ``PLR0912`` / ``PLR0913`` (type-dispatch functions
    are naturally branchy; xenon caps the worst case absolutely
    without requiring per-function `noqa`).
  - **`jscpd`** for cross-file DRY violations
    (`make quality-duplication`). The one category nothing in the
    stack covered. Threshold 1.0% — v1.0.2 baseline 0.36% (7 clones,
    112 lines, all 11–22 lines and structurally template-shaped).
    Wired via `npx -y jscpd@4` (major-version pin for reproducibility)
    so the Python project takes on no permanent JS dep.
  - **`vulture`** wired into the new `make quality-dead-code`
    target. The dev-dep + `[tool.vulture]` config already existed;
    it was never invoked. New `.vulture_whitelist.py` documents the
    one current false positive (the reserved `use_cache` kwarg on
    `execute_query_job`). Each future whitelist entry lands via PR
    review only — that's the contract that keeps the gate
    trustworthy.

  All three run via a single `make quality` umbrella and the new
  `.github/workflows/code-quality.yml` workflow (per-PR + push-to-main
  + nightly + manual). Every step uses `continue-on-error: true` —
  the gates are **non-blocking by design** for this PR. `make verify`
  is unchanged. Promote-to-required is a separate follow-up PR once
  the thresholds settle against `main`. See
  [ADR 0035](docs/adr/0035-code-quality-gates.md) for the full design,
  baselines, and follow-up checklist.

### Changed

- **lychee retry budget bumped** in `.lychee.toml` — `max_retries` 2→4,
  `retry_wait_time` 3s→10s. Calibrated against the v1.0.2 release CI
  cycle (PR #43, 2026-05-23) where lychee hit repeated `502 Bad Gateway`
  from `github.com/.../blob/main/...` source-tree URLs and the
  prior 2×3s retry budget couldn't outlast the transient window.
  Four retries × 10s give a ~40-second total budget per failing URL,
  which absorbs the typical 30-60s blob-render transient without
  masking real outages (adding `502` to the `accept` list was
  rejected — a renamed repo would 502 persistently and we want to
  know).

- **Cyclomatic-complexity gate ratcheted from rank E to rank C +
  promoted to required.** The `quality-complexity` gate introduced by
  ADR 0035 (non-blocking, `xenon --max-absolute E`) now enforces
  `xenon --max-absolute C --max-modules C --max-average A` on
  `src/bqemulator/`. Every function must rank C or better
  (cyclomatic complexity ≤ 20); no exclusions. Ten D-rank and
  E-rank functions surfaced by the baseline audit were refactored
  using two patterns documented in ADR 0036: dispatch-table for
  type-keyed branching (the arrow_bridge / avro_serializer /
  classify_statement_type / scripting parser cases) and helper
  extraction for procedural sub-blocks (the catalog cascade /
  interval parser / write-append post-processor / table-meta
  builder). Worst-case complexity drops from 39 (`_format_bq_value`)
  to 14. The gate is now part of `make verify`, and
  `.github/workflows/code-quality.yml`'s complexity step drops
  `continue-on-error: true`. The branch-protection ruleset's
  required-checks list will be updated to include the `Quality
  gates` job (stable name across future blocking-status changes)
  in a follow-up step once CI on this PR reports the new check
  name green — adding it pre-CI would create a chicken-and-egg
  merge block. Duplication
  (jscpd) + dead-code (vulture) gates stay non-blocking — each gets
  its own ratchet PR when its baseline settles. See
  [ADR 0036](docs/adr/0036-complexity-ratchet-to-c.md) for the full
  audit table, per-function refactor results, and the bucket-A /
  bucket-B refactor patterns.

- **`scripts/bump_version.py` now bumps the README's shields.io
  PyPI / Python-versions badge cache-bust suffix in lockstep with
  `__version__`.** The two badge URLs in `README.md` carry a
  `?cacheSeconds=120&v=X.Y.Z` query param — the `v=` portion is
  ignored by shields.io but is the cache key GitHub camo (the image
  proxy) uses, so bumping it forces readers' browsers to refetch the
  fresh PyPI version on release. Previously that bump was a manual
  README edit on the release branch (see PR #41, where v1.0.1 → 1.0.1
  cache-bust was hand-applied); now both `python scripts/bump_version.py
  X.Y.Z` and the orchestrator `scripts/release.py --apply` rewrite the
  badges idempotently. New `bump.update_readme_text` /
  `bump.write_readme_badges` helpers plus `--readme` CLI flag for
  test isolation; `scripts/release.py --dry-run` previews the badge
  diff alongside the `__init__.py` bump. Idempotent (silent no-op
  when README is missing or already at the target version). Closes
  follow-up "automate README badges + OpenSSF Scorecard" — PR A.

### Fixed

- **`_coerce_arrow_binary` (REST `tabledata.insertAll` BYTES path):
  strict base64 validation + clear errors for non-string inputs.**
  Previously the helper called `base64.b64decode(value)` without
  `validate=True`, which silently produced partial bytes on malformed
  input, AND fell through to `bytes(value)` for any non-string type
  (which would convert iterables of ints — masking real caller
  bugs). Now: strings go through `base64.b64decode(value, validate=True)`
  (matching BigQuery's `400 invalid: Could not decode bytes` on bad
  payloads); `bytes` / `bytearray` are passed through unchanged
  (preserves the Storage Write proto path in
  `streaming/proto_deserializer.py` that feeds proto-decoded BYTES
  fields here); any other type raises `TypeError` with a clear
  message. New unit tests cover all three branches plus the
  pre-existing happy path.
- **INTERVAL literal parsing: bad integer tokens now raise
  `ValidationError` instead of leaking `ValueError`.** The
  compound-parser path (`_consume_blocks` and helpers) used bare
  `int(...)` calls on user-supplied tokens; the wrapping
  `parse_interval_literal` documented `ValidationError` as the
  parse-failure exception but the bare conversion bypassed it on
  the `_consume_blocks` path. A new `_parse_int_token(raw, field=...)`
  helper centralises the `int(...) + try/except ValueError →
  raise ValidationError` pattern; all year / month / day / hour /
  minute call sites now route through it. New
  `test_invalid_inputs_raise` cases cover the year, month, day,
  hour, and minute parse-failure paths that previously leaked
  `ValueError`.

## [1.0.2] — 2026-05-23

### Fixed

- **scio example: end-to-end ``CustomersPipeline.run`` against
  bqemulator** (#17). The v1.0.1 wiring-only smoke flips to a real
  Beam BigQueryIO BATCH_LOADS round-trip — 3 rows written, 3 rows
  read back via ``jobs.query``. No emulator-side code change: the
  fix is contained in the scio example via (a) a new
  ``EmulatorBigQueryServices`` class in ``org.apache.beam.sdk.io.gcp.bigquery``
  that supplies the Apiary ``Bigquery`` client with
  ``setRootUrl(emulator)``, attached via
  ``BigQueryIO.Write.withTestServices(...)`` because Beam 2.55.1's
  Java SDK has no native ``BIGQUERY_EMULATOR_HOST`` support
  ([apache/beam#34037](https://github.com/apache/beam/pull/34037)
  is Go-side only); (b) a ``fsouza/fake-gcs-server`` testcontainers
  sidecar that handles Beam's GCS staging step for the BATCH_LOADS
  write method, bind-mounted into a shared directory with
  bqemulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim so the
  LOAD-job source URIs resolve to the same physical bytes Beam
  staged; (c) ``--gcpCredentialFactoryClass=NoopCredentialFactory``
  to short-circuit OAuth2 refresh against ``oauth2.googleapis.com``;
  (d) a ``testcontainers`` 1.20.4 → 1.21.4 bump because Docker 29+
  rejects docker-java clients announcing API < 1.40. See
  [ADR 0034](docs/adr/0034-scio-beam-emulator-routing.md) for the
  full design; the
  [scio example README](docs/examples/java/scio/README.md) has the
  user-facing recipe.

## [1.0.1] — 2026-05-23

### Fixed

- **Storage Read API IPC framing** (#15). The gRPC ``ReadRows`` handler
  previously packed a full Arrow IPC stream (schema-message + batches +
  EOS-marker) into ``ArrowRecordBatch.serialized_record_batch``, breaking
  every real Storage Read client — ``google-cloud-bigquery-storage``'s
  ``reader.to_arrow(session)`` tripped on ``OSError: Expected IPC message
  of type record batch but got schema``. The handler now emits only the
  record-batch IPC message bytes; the schema continues to travel
  separately via ``ReadSession.arrow_schema.serialized_schema`` and the
  first ``ReadRowsResponse.arrow_schema`` field, matching the BigQuery
  contract. ``serialize_arrow_ipc(table)`` in
  ``bqemulator.streaming.read_session`` is replaced by
  ``serialize_arrow_record_batch(batch)``; the pyspark-bigquery example
  drops its inline workaround and goes back to the natural
  ``reader.to_arrow(session)`` call. See ADR 0033 for the formal
  bare-message contract — dictionary-encoded columns at any nesting
  depth are rejected with ``ValueError`` at the producer boundary.

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
  `VIEWS`, `MATERIALIZED_VIEWS`, `PARTITIONS`, `TABLE_OPTIONS`, etc.)
  queryable via the standard SQL path. The `JOBS` / `JOBS_BY_*` family is
  intentionally out of scope — see
  [`out-of-scope.md#information_schemajobs-family`](docs/reference/out-of-scope.md#information_schemajobs-family).
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
  data directory; backup / restore via `bqemulator backup` and
  `bqemulator restore`.
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

### Known limitations (snapshot at v1.0.0 release; status as of v1.0.1)

The two caveats below were carried at v1.0.0 as documented
limitations to be addressed in v1.0.1. Status update after the
v1.0.1 release:

- ✅ **Storage Read API IPC bytes layout** — **CLOSED in v1.0.1**
  (see ``Fixed`` block under [Unreleased]/[1.0.1] above). The
  ``ReadRows`` handler now emits a bare record-batch IPC message
  per the BigQuery wire contract;
  ``google-cloud-bigquery-storage``'s
  ``reader.to_arrow(session)`` works unchanged; the
  ``python/pyspark-bigquery`` example dropped its inline
  workaround. See [ADR 0033](docs/adr/0033-storage-read-arrow-ipc-bare-message-contract.md)
  for the formal contract. ([#15](https://github.com/jjviscomi/bqemulator/issues/15))
- ✅ **Scio test exercises wiring only** — **CLOSED in v1.0.2**
  (see ``Fixed`` block under [Unreleased] above). The
  ``CustomersPipelineSpec`` now drives ``CustomersPipeline.run``
  end-to-end: 3 rows written via Beam BigQueryIO BATCH_LOADS, 3
  rows read back via ``jobs.query``. The v1.0.1 hypothesis that
  ``--bigQueryEndpoint`` worked turned out to be wrong (Beam
  Java SDK 2.55.1 has no such option — only the Go SDK has
  ``BIGQUERY_EMULATOR_HOST``); the actual fix uses
  ``BigQueryIO.Write.withTestServices(EmulatorBigQueryServices(
  endpoint))`` from a Beam-package-scoped helper in the scio
  example, plus a ``fake-gcs-server`` sidecar bind-mounted into
  bqemulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim for the
  BATCH_LOADS staging step. See
  [ADR 0034](docs/adr/0034-scio-beam-emulator-routing.md) for the
  decision record. ([#17](https://github.com/jjviscomi/bqemulator/issues/17))

