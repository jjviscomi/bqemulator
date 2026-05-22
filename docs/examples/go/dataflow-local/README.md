# Dataflow-style ETL against `bqemulator` (Go)

A Dataflow-shaped Go program that:

1. Reads JSON records from `stdin` (mimicking a Pub/Sub or GCS source).
2. Transforms them.
3. Writes results to BigQuery via the standard `cloud.google.com/go/bigquery`
   client, which routes through `bqemulator`.

This is the local-iteration story for Dataflow batch jobs: identical
code path, no Beam runner needed, fast iteration against an emulator.

## Why a separate example from `beam-pipeline`

- `beam-pipeline/` shows the Apache Beam Go SDK shape.
- This example shows a much simpler shape that many teams actually use
  in production for streaming ETL: a long-running Go binary that
  consumes from a source, transforms, and writes to BigQuery directly.

Both are valuable references — they map to different real-world team
preferences.

## Layout

```
go.mod
cmd/run/main.go         — binary entry point (`go run ./cmd/run`)
etl.go                  — Transform + Sink helpers (testable)
etl_test.go             — table-driven tests + end-to-end against emulator
```

## Run

```bash
make test
```

Requires Docker + Go 1.22+.

## What to look for

- `etl.go` is unit-testable without any Docker: pure transform logic,
  separated from BigQuery I/O.
- `etl_test.go` includes both unit tests and one end-to-end test
  driven by Testcontainers — keeping the fast and slow gates distinct.
- `option.WithEndpoint(...)` is passed the **full base URL**
  (`http://host:port/bigquery/v2/`), not just the host. The Google
  Cloud Go BQ client treats `WithEndpoint` as the full base — it
  replaces the generated `/bigquery/v2/` prefix outright, unlike the
  Python client which appends.
