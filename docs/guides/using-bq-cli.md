# Using the `bq` CLI against bqemulator

[`bq`](https://cloud.google.com/bigquery/docs/bq-command-line-tool-reference)
is Google's official command-line tool for BigQuery. It ships as
part of the `google-cloud-cli` package and is the canonical CLI
surface for data engineers, DBAs, CI pipelines, and ad-hoc shell
scripts that drive BigQuery from a terminal.

bqemulator's REST surface is byte-compatible with what `bq` talks to,
so the same `bq` invocations you run against real BigQuery work
unchanged against a running emulator — once you tell `bq` where to
find it.

This guide shows the three documented ways to point `bq` at the
emulator, the auth bypass you need so anonymous calls don't get
rejected by `bq`'s own credentials machinery, and a runnable
quickstart you can copy-paste.

## Prerequisites

1. **Install the gcloud SDK.** Follow the [official install
   docs](https://cloud.google.com/sdk/docs/install). After install:

 ```bash
    bq version
    # → This is BigQuery CLI 2.x.y
    ```

2. **Start the emulator** in one terminal:

 ```bash
    bqemulator start --ephemeral
    # listens on http://localhost:9050 (REST) and localhost:9060 (gRPC)
    ```

3. **Disable real-credentials lookups** so `bq` doesn't try to
   contact `oauth2.googleapis.com`:

 ```bash
    export CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true
    ```

   Without this, `bq` will fail with an authentication error before
   it ever sends a request to your local emulator.

## Three ways to point `bq` at the emulator

### 1. The `--api=` flag (per invocation)

The simplest path — pass `--api=` to every `bq` call:

```bash
bq --api=http://localhost:9050 query --use_legacy_sql=false 'SELECT 1'
```

This is what the
[`tests/e2e/bq_cli_client/`](https://github.com/jjviscomi/bqemulator/tree/main/tests/e2e/bq_cli_client)
suite uses internally — predictable and trivially isolated per
invocation.

### 2. `gcloud config set api_endpoint_overrides/bigquery`

Persists the endpoint override in your active gcloud configuration:

```bash
gcloud config set api_endpoint_overrides/bigquery http://localhost:9050/
bq query --use_legacy_sql=false 'SELECT 1'
```

To remove the override:

```bash
gcloud config unset api_endpoint_overrides/bigquery
```

Note the trailing slash — gcloud's endpoint override format expects
it.

### 3. A custom `~/.bigqueryrc`

The lowest-disruption option for one-off testing:

```
# ~/.bigqueryrc
api = http://localhost:9050
project_id = my-project
```

`bq` reads this file at startup; you can keep your real gcloud
config untouched.

To isolate from your normal bq config entirely (recommended for CI
pipelines and test sessions), point `CLOUDSDK_CONFIG` at a fresh
directory and drop a `bigqueryrc` there:

```bash
export CLOUDSDK_CONFIG=/tmp/bq-emulator-config
mkdir -p "$CLOUDSDK_CONFIG"
cat > "$CLOUDSDK_CONFIG/bigqueryrc" <<EOF
api = http://localhost:9050
project_id = my-project
EOF
bq query 'SELECT 1'
```

## Quickstart: dataset → table → load → query → cleanup

```bash
export CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true
EMU=http://localhost:9050

# Create a dataset and table.
bq --api=$EMU mk --dataset --location=US my-project:demo
bq --api=$EMU mk --table my-project:demo.customers id:INTEGER,name:STRING

# Load a 3-row NDJSON file.
cat > /tmp/customers.ndjson <<EOF
{"id": 1, "name": "Alice"}
{"id": 2, "name": "Bob"}
{"id": 3, "name": "Carol"}
EOF
bq --api=$EMU load \
    --source_format=NEWLINE_DELIMITED_JSON \
    my-project:demo.customers /tmp/customers.ndjson

# Query.
bq --api=$EMU query --use_legacy_sql=false --format=json \
    'SELECT COUNT(*) AS n FROM `my-project.demo.customers`'

# Clean up.
bq --api=$EMU rm -r -f -d my-project:demo
```

A runnable, CI-verified variant of this lives at
[`docs/examples/bq-cli-quickstart/`](https://github.com/jjviscomi/bqemulator/tree/main/docs/examples/bq-cli-quickstart).

## Common `bq` commands and what REST API they exercise

| `bq` command | REST surface | Notes |
|---|---|---|
| `bq mk --dataset` | `datasets.insert` | `--location=US` recommended for compatibility |
| `bq mk --table` | `tables.insert` | Schema string or `--schema_file=path.json` |
| `bq mk --time_partitioning_field=ts` | `tables.insert` | Partitioning round-trips |
| `bq query --use_legacy_sql=false` | `jobs.insert` (QueryJob) | Use `--format=json` for machine-parseable output |
| `bq query --dry_run` | `jobs.insert` (dryRun: true) | Reports schema/statementType without execution |
| `bq query --parameter=name:TYPE:value` | `jobs.insert` (queryParameters) | Typed scalar parameters |
| `bq load` | upload + `jobs.insert` (LoadJob) | Routes through `/upload/bigquery/v2/...` |
| `bq insert` | `tabledata.insertAll` | Reads NDJSON from stdin |
| `bq head -n N` | `tabledata.list` | Same path SDK suites exercise |
| `bq extract` | `jobs.insert` (ExtractJob) | Avro extract supported |
| `bq cp` | `jobs.insert` (CopyJob) | `--snapshot` / `--clone` for versioning |
| `bq mk --materialized_view` _(via CREATE MATERIALIZED VIEW)_ | DDL through `jobs.insert` | Materialized views |
| `bq show --format=json` | `datasets.get` / `tables.get` | Full resource representation |
| `bq ls` | `datasets.list` / `tables.list` | `--format=json` for parseable output |
| `bq rm` | `datasets.delete` / `tables.delete` | `-r -f -d` for recursive dataset delete |
| `bq update` | `datasets.patch` / `tables.patch` | Description, expiration, schema |

## What `bq` does NOT exercise

- **Storage Read gRPC API** — no `bq` command sets up a
  Storage Read session. Use the Python/Node/Go/Java clients.
- **Storage Write gRPC API** — no `bq` command opens a
  Storage Write stream. Use the SDK clients.
- **Custom request headers** (e.g., `X-Bqemu-Caller` for row access
  policy enforcement) — `bq` doesn't expose a header-injection
  flag. Caller-bound RAP testing happens in the Python suite via
  `AuthorizedSession`.

The
[`tests/e2e/bq_cli_client/test_storage_read_storage_write_skipped.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/e2e/bq_cli_client/test_storage_read_storage_write_skipped.py)
file documents these gaps explicitly via `pytest.skip` with clear
reasons, so future readers see "this isn't tested because it
can't be" instead of "this isn't tested because we forgot."

## Troubleshooting

### `BigQuery error in get operation: Access Denied`

`CLOUDSDK_AUTH_DISABLE_CREDENTIALS` is not set. Export it and retry.

### `BigQuery error: ServerNotFoundError: Unable to find the server`

Either the emulator isn't running, or your `--api=` URL doesn't
include the scheme. Use `http://localhost:9050` not `localhost:9050`.

### `bq` succeeded but returned no rows

Confirm you're not pointed at the real `bigquery.googleapis.com`:

```bash
bq --api=http://localhost:9050 query --use_legacy_sql=false 'SELECT 1'
# Should return a single row with value 1.
```

If the override silently isn't applied, `bq` will hit real BigQuery
and (assuming you have credentials) succeed against a project whose
state is different from your local emulator.

## See also

- [`docs/examples/bq-cli-quickstart/`](https://github.com/jjviscomi/bqemulator/tree/main/docs/examples/bq-cli-quickstart)
  — runnable copy-paste-able example.
- [ADR
  0032](https://github.com/jjviscomi/bqemulator/blob/main/docs/adr/0032-bq-cli-conformance-client.md)
  — design notes for why `bq` is a fifth conformance client and
  what the deliberate gaps are.
- [`tests/e2e/bq_cli_client/`](https://github.com/jjviscomi/bqemulator/tree/main/tests/e2e/bq_cli_client)
  — the 35-test pytest suite that pins every supported `bq` command
  shape.
