# `bq` CLI quickstart (G5)

Runnable five-command `bq` sequence against a fresh bqemulator
instance: create a dataset, create a table, load a 3-row NDJSON
file, query, and clean up.

This example is the cited reference for the
[Using the bq CLI](../../guides/using-bq-cli.md) guide. CI runs
`make test` against this directory so the README's commands cannot
rot.

## Prerequisites

1. `bqemulator` on PATH (`pip install bqemulator`).
2. `bq` on PATH (install the
   [gcloud SDK](https://docs.cloud.google.com/sdk/docs/install)).

## Run

```bash
make test
```

The `run.sh` script:

1. Spins up an ephemeral emulator on a random port via
   `bqemulator start --ephemeral --rest-port 0`.
2. Waits for `/healthz` to return ok.
3. Runs the five `bq` commands below.
4. Asserts the final query returns `n=3`.
5. Tears down the emulator process.

## What gets exercised

```bash
# 1) Create a dataset.
bq --api=$EMU mk --dataset --location=US demo:demo_ds

# 2) Create a table with a schema.
bq --api=$EMU mk --table demo:demo_ds.customers \
    id:INTEGER,name:STRING,email:STRING

# 3) Load 3 NDJSON rows from a local file.
bq --api=$EMU load \
    --source_format=NEWLINE_DELIMITED_JSON \
    demo:demo_ds.customers /tmp/customers.ndjson

# 4) Query.
bq --api=$EMU query --use_legacy_sql=false --format=json \
    'SELECT COUNT(*) AS n FROM `demo.demo_ds.customers`'

# 5) Clean up.
bq --api=$EMU rm -r -f -d demo:demo_ds
```

Each command touches a different REST surface; the load command
goes through the upload host endpoints, and the query command
goes through `jobs.insert` + `jobs.getQueryResults`. A
regression on any of those paths breaks this example — which is
why CI runs it.
