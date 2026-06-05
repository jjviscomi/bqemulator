# Examples

Every example is a complete, runnable project with its own `README.md`
and `make test` job. Each example is CI-verified on every PR so they do
not drift.

## Quickstarts (already shipped)

| Path | What it demonstrates |
|---|---|
| [`bq-cli-quickstart/`](bq-cli-quickstart/README.md) | Five-command `bq` CLI tour (mk dataset → mk table → load → query → rm) |
| [`local-file-load/`](local-file-load/README.md) | `client.load_table_from_file(io.BytesIO(...))` against `/upload/bigquery/v2/...` |

## Python integrations

| Path | What it demonstrates | Known caveats |
|---|---|---|
| [`python/pytest-integration/`](python/pytest-integration/README.md) | Flask app + pytest fixture (`bqemu_client`) | — |
| [`python/export-to-gcs/`](python/export-to-gcs/README.md) | `EXPORT DATA OPTIONS(...) AS SELECT` → CSV in Cloud Storage, read back off the `BQEMU_GCS_LOCAL_ROOT` mount | — |
| [`python/dbt-local/`](python/dbt-local/README.md) | `dbt build` against emulator (seed + model + tests) | — |
| [`python/airflow-dag-test/`](python/airflow-dag-test/README.md) | `BigQueryInsertJobOperator` DAG via `dag.test()` | Monkey-patches `google.auth.default` to return `AnonymousCredentials` so the BQ hook skips JWT grant |
| [`python/pyspark-bigquery/`](python/pyspark-bigquery/README.md) | PySpark `DataFrame` from Storage Read (Arrow) | — (v1.0.0's IPC-format workaround was removed in v1.0.1 — [#15](https://github.com/jjviscomi/bqemulator/issues/15) / [ADR 0033](../adr/0033-storage-read-arrow-ipc-bare-message-contract.md)) |

## Node.js / TypeScript integrations

| Path | What it demonstrates |
|---|---|
| [`nodejs/nestjs-app/`](nodejs/nestjs-app/README.md) | NestJS app + Jest + supertest e2e |
| [`nodejs/cloud-run-local/`](nodejs/cloud-run-local/README.md) | Express service running in a Cloud Run-shaped image |

## Go integrations

| Path | What it demonstrates |
|---|---|
| [`go/beam-pipeline/`](go/beam-pipeline/README.md) | Apache Beam Go SDK DirectRunner |
| [`go/dataflow-local/`](go/dataflow-local/README.md) | Stand-alone Go ETL binary writing to BigQuery |

## Java / Scala integrations

| Path | What it demonstrates | Known caveats |
|---|---|---|
| [`java/spring-boot/`](java/spring-boot/README.md) | Spring Boot repository + Testcontainers integration test | — |
| [`java/scio/`](java/scio/README.md) | Spotify Scio (Scala-on-Beam) pipeline + ScalaTest. End-to-end ``CustomersPipeline.run`` writes 3 rows via Beam's BATCH_LOADS and the emulator returns them on read — closed in v1.0.2 via a per-example ``EmulatorBigQueryServices`` injected through ``BigQueryIO.Write.withTestServices(...)`` and a ``fsouza/fake-gcs-server`` sidecar for the GCS staging step ([#17](https://github.com/jjviscomi/bqemulator/issues/17), [ADR 0034](../adr/0034-scio-beam-emulator-routing.md)). | — |

## Compose stacks

| Path | What it demonstrates |
|---|---|
| [`docker-compose/full-stack/`](docker-compose/full-stack/README.md) | App + emulator + Prometheus + Grafana, one `docker compose up` |

## CI/CD recipes

| Path | What it demonstrates |
|---|---|
| [`ci-recipes/github-actions/`](ci-recipes/github-actions/README.md) | `services:` container pattern + Testcontainers pattern |
| [`ci-recipes/gitlab-ci/`](ci-recipes/gitlab-ci/README.md) | `services:` with the `bqemulator` alias on the CI network |
| [`ci-recipes/circleci/`](ci-recipes/circleci/README.md) | Docker secondary executor + machine executor patterns |

## Structure conventions

Each example provides:

- `README.md` — what it demonstrates, how to run, what to look for.
- `Makefile` with a `test` target run in CI.
- Pinned dependency versions (where applicable).
- A link from the relevant guide in `docs/guides/` (where applicable).

## Running an example locally

Most examples assume:

- `bqemulator` is on `$PATH` (`pip install bqemulator`), **or**
- `docker` can pull `ghcr.io/jjviscomi/bqemulator:dev`.

The README in each subdirectory documents toolchain-specific
prerequisites (Node + npm, Go 1.22+, JDK 17 + Maven, Spark, dbt, etc.).

## CI

Each example has its own job in
[`.github/workflows/examples.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/examples.yml).
The job builds (or pulls) the dev image and runs the example's
`make test`. The examples workflow is **not** a required check on the
main pipeline — a flaky upstream dependency (dbt-core release, Spark
upgrade, etc.) should not block emulator PRs.
