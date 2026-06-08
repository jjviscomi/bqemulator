<div align="center">

# bqemulator

**A local, drop-in emulator for Google BigQuery.**

DuckDB-backed, SQLGlot-powered, and tested against the real service. Point the official Google Cloud client libraries at it and run your BigQuery code on your laptop or in CI — no real project, no billing, no network.

[![CI](https://github.com/jjviscomi/bqemulator/actions/workflows/ci.yml/badge.svg)](https://github.com/jjviscomi/bqemulator/actions/workflows/ci.yml)
[![E2E](https://github.com/jjviscomi/bqemulator/actions/workflows/e2e.yml/badge.svg)](https://github.com/jjviscomi/bqemulator/actions/workflows/e2e.yml)
[![Conformance](https://github.com/jjviscomi/bqemulator/actions/workflows/conformance.yml/badge.svg)](https://github.com/jjviscomi/bqemulator/actions/workflows/conformance.yml)
[![Docs](https://github.com/jjviscomi/bqemulator/actions/workflows/docs.yml/badge.svg)](https://jjviscomi.github.io/bqemulator/)
[![PyPI](https://img.shields.io/pypi/v/bqemulator.svg?cacheSeconds=120&v=1.2.0)](https://pypi.org/project/bqemulator/)
[![Python](https://img.shields.io/pypi/pyversions/bqemulator.svg?cacheSeconds=120&v=1.2.0)](https://pypi.org/project/bqemulator/)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/jjviscomi/bqemulator/badge?v=1.2.0)](https://scorecard.dev/viewer/?uri=github.com/jjviscomi/bqemulator)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

[**Documentation**](https://jjviscomi.github.io/bqemulator/)
 · [**Quickstart**](https://jjviscomi.github.io/bqemulator/latest/getting-started/)
 · [**Examples**](docs/examples/)
 · [**Compatibility matrix**](https://jjviscomi.github.io/bqemulator/latest/reference/compatibility-matrix/)
 · [**Changelog**](CHANGELOG.md)

</div>

---

## Why bqemulator?

Testing code against real BigQuery is **slow** (network + service latency), **expensive** (every query is billable), and **dangerous** (no rollback in shared environments). The alternatives — mocks, fakes, and shared sandboxes — drift from the real service the moment you stop chasing them.

`bqemulator` is a process you can run locally that **speaks BigQuery's actual wire protocol** (REST + gRPC), backs onto a real analytical SQL engine (DuckDB), and translates GoogleSQL → DuckDB SQL with a rule-based, ADR-grounded translator (SQLGlot). The official `google-cloud-bigquery`, `@google-cloud/bigquery`, `cloud.google.com/go/bigquery`, `com.google.cloud:google-cloud-bigquery`, and `bq` CLI clients all work against it unchanged — only the endpoint differs.

Three use cases, one binary:

- **Ephemeral CI fixture** — `pytest` plugin starts an in-process emulator on a random port; `pip install bqemulator[testing]` is all the wiring you need.
- **Long-running local dev server** — `bqemulator start --data-dir ~/bqemu` persists state across runs; works with the official `bq` CLI, dbt, Airflow, PySpark, Beam, Scio.
- **Offline replica of a real project** — `bqemulator import --from-project <id>` clones schema (and optionally data) from real BigQuery into a local data directory.

## Highlights

- 🟢 **Full REST + gRPC API parity** — Datasets, Tables, Jobs, TableData, Routines, Row Access Policies, Authorized Views, plus Models CRUD metadata. Storage Read API (Arrow and Avro). Storage Write API (all four stream types — `DEFAULT`, `COMMITTED`, `PENDING`, `BUFFERED` — with both proto and Arrow row formats).
- ⚡ **Real SQL** — GoogleSQL translated to DuckDB SQL via 92 SQLGlot rules + 24 rewriters; covers date/time, string, array, struct, range, geography, JSON, approximate-aggregate, statistical, regex, civil-time, and bit operations.
- 🧠 **Features `goccy/bigquery-emulator` doesn't have** — JavaScript UDFs (embedded V8 via `mini-racer`), procedural scripting (`DECLARE` / `BEGIN…END` / `IF` / `LOOP` / `EXCEPTION` / `BEGIN TRANSACTION`), time travel (`FOR SYSTEM_TIME AS OF`), table snapshots, table clones, materialized views with refresh dispatch, GEOGRAPHY (planar via DuckDB-spatial + S2 helpers), RANGE, INTERVAL, authorized views, row-access policies, `INFORMATION_SCHEMA`.
- 🔌 **Five-client e2e matrix** — every release is exercised against the official Python, Node.js, Go, and Java BigQuery client libraries plus Google's `bq` CLI in a live Docker container.
- 🧪 **7-tier test pyramid** — unit + property + integration + conformance + e2e + perf + chaos, plus mutation / fuzz / differential siblings. Combined coverage is gated at ≥90% line + branch.
- 📐 **Conformance corpus** — 1,288 fixtures recorded against real BigQuery. Drift between the emulator and the real service surfaces as a failing test; documented divergences are pinned with ADR references.
- 🐍 **Native pytest plugin** — `pip install bqemulator` registers a pytest plugin; the `bqemu_server` fixture starts an ephemeral in-process emulator on random free ports and sets `BIGQUERY_EMULATOR_HOST`. No `conftest.py` wiring required.
- 🐳 **Multi-arch container** — `ghcr.io/jjviscomi/bqemulator` builds for `linux/amd64` + `linux/arm64`, with cosign keyless signatures via GitHub OIDC.
- 🔭 **Production-grade observability** — `structlog` JSON logs, OpenTelemetry tracing (configurable OTLP exporter), Prometheus metrics endpoint.

## Install

```bash
pip install bqemulator
```

Optional extras:

```bash
pip install "bqemulator[testing]"      # pytest, hypothesis, testcontainers, bigquery client
pip install "bqemulator[udf-js]"       # JavaScript UDF support (embedded V8)
pip install "bqemulator[orc]"          # ORC format for load jobs
pip install "bqemulator[compression]"  # zstd + snappy for load/extract jobs
pip install "bqemulator[import]"       # bqemulator import --from-project
pip install "bqemulator[all]"          # all runtime extras (no testing extras)
```

Docker:

```bash
docker run --rm -p 9050:9050 -p 9060:9060 ghcr.io/jjviscomi/bqemulator:latest
```

Both `pip` and the published image bundle the same emulator. The image exposes REST on `9050` and gRPC on `9060` by default — see [configuration reference](https://jjviscomi.github.io/bqemulator/latest/reference/configuration/) to change them.

> **Windows users:** install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) with the WSL2 backend (default since Docker Desktop 4.x); the published Linux image runs natively under WSL2 with no Windows-specific configuration. Native Windows-container variants of the image are explicitly out of scope for v1.0 — see [docs/reference/out-of-scope.md#native-windows-containers](docs/reference/out-of-scope.md) for the rationale.

## Quickstart

### Python

```python
import os
from google.cloud import bigquery

# Either set BIGQUERY_EMULATOR_HOST (picked up by every Google Cloud library)
# or pass api_endpoint explicitly to the Client. Both work.
os.environ["BIGQUERY_EMULATOR_HOST"] = "localhost:9050"

client = bigquery.Client(project="my-test-project")

client.create_dataset("sales")
client.create_table(
    bigquery.Table(
        "sales.orders",
        schema=[
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("amount", "NUMERIC"),
            bigquery.SchemaField("placed_at", "TIMESTAMP"),
        ],
    )
)
client.insert_rows_json(
    "sales.orders",
    [{"id": 1, "amount": "12.50", "placed_at": "2026-05-21T00:00:00Z"}],
)

for row in client.query("SELECT COUNT(*) AS n FROM sales.orders").result():
    print(row.n)  # 1
```

### pytest

`bqemulator` ships a pytest plugin via the `pytest11` entry point. Installing the package is all the wiring you need — your `conftest.py` stays empty.

```python
from google.cloud import bigquery

def test_orders_table(bqemu_client: bigquery.Client) -> None:
    bqemu_client.create_dataset("sales")
    # ... your test ...
```

The `bqemu_server` fixture is session-scoped (one emulator per test session); the `bqemu_client` fixture is function-scoped and returns a pre-configured `bigquery.Client`. See the [pytest fixture guide](https://jjviscomi.github.io/bqemulator/latest/quickstart/pytest/) and the [`python/pytest-integration`](docs/examples/python/pytest-integration/) example for a complete Flask app with integration tests.

### Node.js

```javascript
const { BigQuery } = require('@google-cloud/bigquery');

const bq = new BigQuery({
  projectId: 'my-test-project',
  apiEndpoint: 'http://localhost:9050',
  token: 'dummy',  // emulator accepts any token
});

await bq.createDataset('sales');
```

See the [Node.js quickstart](https://jjviscomi.github.io/bqemulator/latest/quickstart/nodejs/) and the [`nodejs/nestjs-app`](docs/examples/nodejs/nestjs-app/) example.

### Go

```go
client, _ := bigquery.NewClient(
    ctx, "my-test-project",
    option.WithEndpoint("http://localhost:9050"),
    option.WithoutAuthentication(),
)
```

See the [Go quickstart](https://jjviscomi.github.io/bqemulator/latest/quickstart/go/) and the [`go/beam-pipeline`](docs/examples/go/beam-pipeline/) example.

### Java

```java
BigQuery bq = BigQueryOptions.newBuilder()
    .setProjectId("my-test-project")
    .setHost("http://localhost:9050")
    .setCredentials(NoCredentials.getInstance())
    .build()
    .getService();
```

See the [Java quickstart](https://jjviscomi.github.io/bqemulator/latest/quickstart/java/) and the [`java/spring-boot`](docs/examples/java/spring-boot/) example.

### `bq` CLI

```bash
bq --api=http://localhost:9050 \
   --project_id=my-test-project \
   query --use_legacy_sql=false 'SELECT 1 AS n'
```

See the [`bq` CLI guide](https://jjviscomi.github.io/bqemulator/latest/guides/using-bq-cli/) and the [`bq-cli-quickstart`](docs/examples/bq-cli-quickstart/) example.

### docker-compose

```yaml
services:
  bqemulator:
    image: ghcr.io/jjviscomi/bqemulator:latest
    ports: ["9050:9050", "9060:9060"]
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:9050/healthz"]
      interval: 2s
      retries: 30

  app:
    build: .
    environment:
      BIGQUERY_EMULATOR_HOST: bqemulator:9050
    depends_on:
      bqemulator: { condition: service_healthy }
```

See the [`docker-compose/full-stack`](docs/examples/docker-compose/full-stack/) example for app + emulator + Prometheus + Grafana.

## What works today

`bqemulator` is at **v1.2.0** — second minor on the production-stable
line. SemVer applies: breaking changes ship only in MAJOR,
deprecations live ≥2 MINOR or 6 months. The [compatibility matrix](https://jjviscomi.github.io/bqemulator/latest/reference/compatibility-matrix/) is auto-generated from the conformance corpus on every CI run; the [conformance coverage matrix](https://jjviscomi.github.io/bqemulator/latest/reference/conformance-coverage-matrix/) breaks down support by surface item.

| Surface | Status |
|---|---|
| BigQuery REST: Datasets / Tables / Jobs / TableData / Routines / Row Access Policies / Authorized Views | ✅ |
| Multipart + resumable upload (`/upload/bigquery/v2/...`) | ✅ |
| `INFORMATION_SCHEMA` (TABLES, COLUMNS, ROUTINES, VIEWS, JOBS, JOBS_BY_*, MATERIALIZED_VIEWS, PARTITIONS, TABLE_OPTIONS, …) | ✅ |
| Storage Read API (Arrow + Avro) | ✅ |
| Storage Write API (all 4 stream types, proto + Arrow row formats) | ✅ |
| GoogleSQL function surface (date / time / string / array / struct / JSON / regex / aggregate / approx / civil-time / bit) | ✅ |
| Procedural scripting (`DECLARE`, `BEGIN…END`, `IF`, `LOOP`, `EXCEPTION`, `BEGIN TRANSACTION`) | ✅ |
| SQL / JavaScript / Table-valued UDFs | ✅ |
| Time travel (`FOR SYSTEM_TIME AS OF`), snapshots, clones, materialized views | ✅ |
| Authorized views + row access policies + caller identity | ✅ |
| GEOGRAPHY / RANGE / INTERVAL / NUMERIC / BIGNUMERIC types | ✅ |
| Load formats: CSV / JSON / Avro / ORC / Parquet | ✅ |
| Extract formats: CSV / JSON / Avro / Parquet | ✅ |
| SQL `EXPORT DATA` to Cloud Storage (CSV / JSON / Avro / Parquet, size-based wildcard sharding) | ✅ |
| BigQuery ML (`CREATE MODEL`, `ML.PREDICT`, …) | ❌ Out of scope — see [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md) |
| BI Engine / slot reservations / Data Transfer Service / scheduled queries | ❌ Out of scope |

**Conformance corpus depth** (the [conformance coverage matrix](https://jjviscomi.github.io/bqemulator/latest/reference/conformance-coverage-matrix/) carries the live, auto-generated breakdown):

| Status | Surface items | % of deterministic surface |
|---|---|---|
| 🟢🟢 Deep (≥6 fixtures) | 102 | 24.9% |
| 🟢 Covered (3–5 fixtures) | 68 | 16.6% |
| 🟡 Sampled (1–2 fixtures) | 238 | 58.2% |
| 🔴 Uncovered (0 fixtures) | 1 | 0.2% |
| **Total** | **409** | 100.0% |

Plus **10 non-deterministic items** (`RAND`, `CURRENT_*`, `SESSION_USER`, `GENERATE_UUID`, `TABLESAMPLE`, `FOR SYSTEM_TIME AS OF <expression>`) that are excluded from the conformance corpus by [ADR 0022](docs/adr/0022-conformance-corpus-design.md) and exercised in unit / property / integration tiers instead — bringing the full inventory to **419 surface items across 20 categories**, backed by a **1,288-fixture conformance corpus** (1,213 SQL + 49 HTTP + 26 gRPC) under `tests/conformance/`.

We follow a **no-deferral principle**: features either ship complete or are excluded with documented rationale. There is no "TODO for v1.1." Scope boundaries are catalogued in [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md).

## Documentation

The full documentation lives at **[jjviscomi.github.io/bqemulator](https://jjviscomi.github.io/bqemulator/)**. Key entry points:

- [**Getting started**](https://jjviscomi.github.io/bqemulator/latest/getting-started/) — your first ten minutes.
- [**Per-language quickstarts**](https://jjviscomi.github.io/bqemulator/latest/quickstart/python/) — Python · Node.js · Go · Java · pytest · docker-compose · Testcontainers.
- [**Guides**](https://jjviscomi.github.io/bqemulator/latest/guides/loading-data/) — loading data, querying, streaming inserts, Storage API, UDFs, scripting, partitioning, time travel, materialized views, row access policies, dbt, Airflow, Spark, the `bq` CLI, observability, and more.
- [**Reference**](https://jjviscomi.github.io/bqemulator/latest/reference/configuration/) — configuration, CLI, REST coverage, SQL function mapping, compatibility matrix, conformance coverage matrix, out-of-scope catalogue, troubleshooting.
- [**Architecture**](https://jjviscomi.github.io/bqemulator/latest/architecture/overview/) — hexagonal architecture, storage model, SQL translation, jobs lifecycle, Storage Read/Write API design, scripting, UDFs, versioning, row access, specialized types, observability, testing strategy, conformance tier.
- [**ADRs**](https://jjviscomi.github.io/bqemulator/latest/adr/0001-use-duckdb/) — 43 Architecture Decision Records documenting every non-obvious design choice.

## Examples

Every example under [`docs/examples/`](docs/examples/) is a complete, runnable project with its own `make test` validated by CI:

| Toolchain | Example | What it demonstrates |
|---|---|---|
| Python | [`python/pytest-integration`](docs/examples/python/pytest-integration/) | Flask app + auto-discovered `bqemu_client` fixture |
| Python | [`python/dbt-local`](docs/examples/python/dbt-local/) | `dbt build` cycle via endpoint override |
| Python | [`python/airflow-dag-test`](docs/examples/python/airflow-dag-test/) | `BigQueryInsertJobOperator` DAG via offline `dag.test()` |
| Python | [`python/pyspark-bigquery`](docs/examples/python/pyspark-bigquery/) | Storage Read → Arrow → Spark DataFrame |
| Node.js | [`nodejs/nestjs-app`](docs/examples/nodejs/nestjs-app/) | NestJS + Jest + supertest e2e |
| Node.js | [`nodejs/cloud-run-local`](docs/examples/nodejs/cloud-run-local/) | Cloud Run-shaped Express + docker-compose |
| Go | [`go/beam-pipeline`](docs/examples/go/beam-pipeline/) | Apache Beam Go SDK + Testcontainers |
| Go | [`go/dataflow-local`](docs/examples/go/dataflow-local/) | Stand-alone Go ETL binary |
| Java | [`java/spring-boot`](docs/examples/java/spring-boot/) | Spring Boot + Testcontainers |
| Scala | [`java/scio`](docs/examples/java/scio/) | Spotify Scio (Scala-on-Beam) pipeline |
| Compose | [`docker-compose/full-stack`](docs/examples/docker-compose/full-stack/) | App + emulator + Prometheus + Grafana |
| CI | [`ci-recipes/github-actions`](docs/examples/ci-recipes/github-actions/) | Service-container + Testcontainers patterns |
| CI | [`ci-recipes/gitlab-ci`](docs/examples/ci-recipes/gitlab-ci/) | `services:` alias on the CI network |
| CI | [`ci-recipes/circleci`](docs/examples/ci-recipes/circleci/) | Docker-secondary + machine executor |

## Project status

`bqemulator` is at **v1.2.0** — second minor on the production-stable
line. SemVer applies: breaking changes ship only in MAJOR
versions, preceded by ≥1 MINOR with deprecation warnings;
deprecated APIs remain for ≥2 MINOR versions or 6 months.

Maturity signals:

- ✅ 43 Architecture Decision Records covering every non-obvious design choice (`docs/adr/0001`–`0043`).
- ✅ ≥90% line + branch coverage gated by CI (`make verify`).
- ✅ 7 test tiers passing (unit + property + integration + conformance + e2e + perf + chaos).
- ✅ 5-client e2e matrix (Python · Node.js · Go · Java · `bq` CLI).
- ✅ Mutation-tier (`mutmut`) pilot landed on pure-domain modules.
- ✅ Fuzz-tier (`Atheris`) harnesses on the SQL translator, dynamic-protobuf decoder, and Arrow bridge.
- ✅ Differential-tier row-order perturbation of the entire conformance corpus passes.
- ✅ Performance baselines committed for `darwin-arm64`, with regression gates (`pytest-benchmark` `--benchmark-compare-fail=median:10%`).
- ✅ PyPI publish via Trusted Publishing (sigstore-attested wheels) — `pip install bqemulator==1.2.0` resolves from [PyPI](https://pypi.org/project/bqemulator/).
- ✅ GHCR publish with keyless cosign signatures — `docker pull ghcr.io/jjviscomi/bqemulator:1.2.0` resolves and the image is cosign-verifiable.

See [`CHANGELOG.md`](CHANGELOG.md) for the complete release-by-release inventory.

## Contributing

We welcome contributions of all sizes. Start with **[CONTRIBUTING.md](CONTRIBUTING.md)** for the mechanics; **[AGENTS.md](AGENTS.md)** captures the project's day-to-day conventions; and **[`docs/architecture/overview.md`](docs/architecture/overview.md)** is the canonical architectural reference.

Pull requests are squash-merged into `main` with a Conventional Commits subject; commits carry a [DCO](https://developercertificate.org/) sign-off (`git commit -s`). The full review policy lives in [GOVERNANCE.md](GOVERNANCE.md).

## Community

- 💬 **[GitHub Discussions](https://github.com/jjviscomi/bqemulator/discussions)** — design questions, usage questions, and general help.
- 🐛 **[Issues](https://github.com/jjviscomi/bqemulator/issues)** — bug reports and feature requests. Please search existing issues first.
- 🔒 **[Security advisories](https://github.com/jjviscomi/bqemulator/security/advisories/new)** — report vulnerabilities privately via the GitHub Security Advisory flow (see [SECURITY.md](SECURITY.md) for our disclosure policy).
- 📜 **[Code of Conduct](CODE_OF_CONDUCT.md)** — adapted from the Contributor Covenant 2.1.

## License

`bqemulator` is released under the **[Apache License 2.0](LICENSE)**.

## Acknowledgements

- [`goccy/bigquery-emulator`](https://github.com/goccy/bigquery-emulator) for blazing the trail and providing a decade of issue reports that seeded our regression corpus.
- [DuckDB](https://duckdb.org/), [SQLGlot](https://github.com/tobymao/sqlglot), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), [Hatchling](https://hatch.pypa.io/), and the Google Cloud client library teams whose work makes this project tractable.
- The [Apache Beam](https://beam.apache.org/), [dbt](https://www.getdbt.com/), [Airflow](https://airflow.apache.org/), [PySpark](https://spark.apache.org/), [Spotify Scio](https://spotify.github.io/scio/), [NestJS](https://nestjs.com/), and [Spring Boot](https://spring.io/projects/spring-boot) communities whose work the example projects compose with.
