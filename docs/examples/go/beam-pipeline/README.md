# Apache Beam Go SDK pipeline against `bqemulator`

A minimal Beam Go pipeline (Direct runner) that writes a small table to
BigQuery and reads it back. Targets `bqemulator` by setting the
`--bigquery_endpoint` pipeline flag.

## What it demonstrates

- Constructing a `BigQueryIO.Write` transform with `WRITE_TRUNCATE`
  and `CREATE_IF_NEEDED` against the emulator.
- Reading back with `BigQueryIO.Read` and asserting the row count via
  `passert`.
- Driving the test from a Go test (`TestMain`) that starts the
  emulator via Testcontainers-go and passes the dynamic host to the
  pipeline.

## Layout

```
go.mod
main.go                     — pipeline binary (cmd entry point)
pipeline.go                 — pipeline construction
pipeline_test.go            — end-to-end test with testcontainers-go
```

## Run

```bash
make test
```

Requires Docker + Go 1.22+.

## What to look for

- The pipeline is plain Beam Go — no emulator-specific imports.
- `pipeline_test.go` is the **only** test, and it drives the whole
  pipeline. This matches the recommended Beam Go integration test
  pattern.
