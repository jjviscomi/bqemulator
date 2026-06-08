# CLI

`bqemulator` is the console entry point.

## Global

```
bqemulator [OPTIONS] COMMAND [ARGS]...

  bqemulator — local emulator for Google BigQuery.

Options:
  -V, --version  Show version and exit.
  -h, --help     Show help and exit.

Commands:
  start    Start the emulator (REST + gRPC).
  import   Mirror schemas from a real BigQuery project.
  version  Print version and exit.
```

## `bqemulator start`

See [configuration](configuration.md) for every flag and env var.

Common invocations:

```bash
# Ephemeral, default ports — fastest start for tests
bqemulator start --ephemeral

# Persistent on a chosen path
bqemulator start --persistent --data-dir ~/.bqemulator

# Random free ports (for parallel test workers)
bqemulator start --rest-port 0 --grpc-port 0

# Pretty logs for local development
bqemulator start --log-format console --log-level debug
```

## `bqemulator import`

Requires the `import` extra (`pip install 'bqemulator[import]'`).

```bash
bqemulator import \
    --from-project=real-project-id \
    --data-dir ~/.bqemulator \
    --dataset sales --dataset marketing
```

Mirrors the schemas of the listed datasets (or all datasets if
`--dataset` is omitted) from a real BigQuery project into the local
catalog. No row data is copied.

## `bqemulator version`

```
bqemulator 1.2.0
```

## Using Google's `bq` CLI against the emulator

Google's [`bq`](https://cloud.google.com/bigquery/docs/reference/bq-cli-reference)
command-line tool talks to bqemulator unchanged. The full
configuration recipe — three endpoint-override paths, the
required auth bypass, a runnable five-command quickstart — lives
in the [Using the bq CLI](../guides/using-bq-cli.md) guide.

`bq` is a separate binary from `bqemulator`; install it via the
[gcloud SDK](https://cloud.google.com/sdk/docs/install). The
quickest first call:

```bash
export CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true
bq --api=http://localhost:9050 query --use_legacy_sql=false 'SELECT 1'
```

As of G5, `bq` is the fifth conformance client in
the E2E matrix — see
[ADR 0032](../adr/0032-bq-cli-conformance-client.md) and the
[`tests/e2e/bq_cli_client/`](https://github.com/jjviscomi/bqemulator/tree/main/tests/e2e/bq_cli_client)
suite.
