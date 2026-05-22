# Apache Beam Go SDK pipeline against `bqemulator`

A minimal Beam Go pipeline (Direct runner) that seeds a customers
table via the standard BigQuery client and runs a trivial Beam
PCollection over those rows. Demonstrates the Beam Go SDK shape
without relying on Beam's in-development BigQueryIO emulator
support.

## What it demonstrates

- Seeding a dataset + table + rows against the emulator via the
  `cloud.google.com/go/bigquery` client (`Seed`).
- Constructing a Beam pipeline from a fixed slice
  (`BuildCountPipeline`) and executing it on the Direct runner.
- Driving the test from a Go test that starts the emulator via
  Testcontainers-go.

## Layout

```
go.mod
cmd/run/main.go             — pipeline binary (`go run ./cmd/run`)
pipeline.go                 — `Seed` + `BuildCountPipeline`
pipeline_test.go            — end-to-end test with testcontainers-go
```

## Run

```bash
make test
```

Requires Docker + Go 1.22+.

## What to look for

- `option.WithEndpoint(...)` is passed the **full base URL**
  (`http://host:port/bigquery/v2/`), not just the host. The Google
  Cloud Go BQ client treats `WithEndpoint` as the full base — it
  replaces the generated `/bigquery/v2/` prefix outright, unlike the
  Python client which appends.
- `BuildCountPipeline` returns the input PCollection directly rather
  than chaining a `beam.Count` — that helper was removed from
  upstream Beam Go SDK. Users who want a row count can chain
  `stats.Count` from `github.com/apache/beam/sdks/v2/go/pkg/beam/transforms/stats`.
- `direct.Execute` returns `(PipelineResult, error)` in modern Beam;
  the test uses `if _, err := direct.Execute(ctx, p); err != nil`.
