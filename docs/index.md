# bqemulator

A local emulator for Google BigQuery. Run it on your laptop or in CI and
point the official Google Cloud client libraries at it.

!!! note "Status"
    **v1.1.2** — production-stable. SemVer applies: breaking changes
    ship only in MAJOR, preceded by ≥1 MINOR with deprecation
    warnings; deprecated APIs remain for ≥2 MINOR or 6 months. See
    the [compatibility matrix](reference/compatibility-matrix.md) for
    feature coverage and the [roadmap](https://github.com/jjviscomi/bqemulator/milestones)
    for what's coming.

## Why bqemulator?

BigQuery tests against real cloud are slow, expensive, and require
network. Mocking the client library misses SQL-dialect mistakes. The
existing [goccy/bigquery-emulator](https://github.com/goccy/bigquery-emulator)
provided a lot of inspiration for this project and how we could approach
the problem differently.

bqemulator is:

- **Python** — `pip install bqemulator` installs prebuilt wheels; no native
  compilation.
- **DuckDB-backed** — real analytical SQL engine, fast, embedded.
- **SQLGlot-powered** — GoogleSQL is translated to DuckDB SQL by a
  maintained transpiler plus our rule layer.
- **API-complete** — REST + gRPC Storage Read + gRPC Storage Write.
- **Feature-complete vs goccy** — JS UDFs, procedural scripting, time
  travel, snapshots, materialized views, GEOGRAPHY, RANGE, row access
  policies, authorized views.
- **pytest-native** — a session fixture ships in the package.
- **Multi-language tested** — every release runs e2e against Python, Node,
  Go, and Java clients in a live Docker container.

## Two-minute demo

```bash
pip install bqemulator
bqemulator start --ephemeral &

python - <<'PY'
import os
from google.cloud import bigquery
os.environ["BIGQUERY_EMULATOR_HOST"] = "localhost:9050"
client = bigquery.Client(
    project="demo",
    client_options={"api_endpoint": "http://localhost:9050"},
)
client.create_dataset("sales")
print("ok")
PY
```

## Where to go next

- [Getting started](getting-started.md)
- [Python quickstart](quickstart/python.md)
- [pytest quickstart](quickstart/pytest.md)
- [Compatibility matrix](reference/compatibility-matrix.md)
- [Out-of-scope features](reference/out-of-scope.md)
- [Architecture overview](architecture/overview.md)
