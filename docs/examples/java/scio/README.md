# Spotify Scio pipeline against `bqemulator`

A minimal [Scio](https://github.com/spotify/scio) pipeline that
writes a customers table to BigQuery and reads it back. The Scala
source under `src/main/scala/` is production-shaped — what you'd run
against real BigQuery or Dataflow today.

## Why this matters

Scio is the canonical Scala-on-Beam idiom — most Beam-on-JVM workloads
on Google's stack run through it. Targeting it against `bqemulator`
means engineering teams can run the same job locally that they run in
Dataflow, without spinning up a real BigQuery dataset.

## Known limitation — Beam Java BigQueryIO doesn't reach the emulator

Beam Java BigQueryIO **does not honour `--bigQueryEndpoint` for the
write path** — that pipeline option only gates internal preflight
validators (dataset-exists checks, schema lookups). The actual write
goes through the official Java BigQuery client at its default base
URL (`https://bigquery.googleapis.com/...`).

The Java BQ client honours the `BIGQUERY_EMULATOR_HOST` env var, but
it has to be visible to the JVM **before the first BQ class loads**.
Testcontainers allocates the bqemulator port dynamically at runtime,
so by the time we know the port the BQ classes are already wired to
the real Google endpoint.

The result: running `CustomersPipeline.run(args)` from inside the
test JVM tries to write to real BigQuery and 404s without
credentials. **The pipeline itself is correct** — point it at a
long-lived bqemulator on a stable port (with `BIGQUERY_EMULATOR_HOST`
exported before the JVM starts) and it runs end to end.

Tracked for cleanup in
[#17](https://github.com/jjviscomi/bqemulator/issues/17) — Scio /
Beam may grow a per-call endpoint override that lets the test set
the endpoint after the container is up.

## What the spec exercises (v1.0.0)

Until #17 lands, the ScalaTest spec exercises the **wiring** that
bqemulator owns and that Scio users actually depend on:

- bqemulator container starts and `/healthz` returns `200`.
- Dataset creation via the REST surface succeeds (status in
  `{200, 201, 409}` — 409 covers idempotent re-runs).
- The dataset shows up on the `/bigquery/v2/projects/{project}/datasets`
  listing endpoint.

The end-to-end `CustomersPipeline.run(args)` assertion will return to
the spec once #17 is resolved.

## Layout

```
build.sbt                                             — sbt build definition
project/build.properties                              — sbt version pin
src/main/scala/com/example/bqemu/CustomersPipeline.scala
src/test/scala/com/example/bqemu/CustomersPipelineSpec.scala
```

## Run

```bash
make test
```

`make test` runs `sbt test`. Requires Docker for the Testcontainers
emulator.

## What to look for

- `build.sbt` holds the Jackson stack at `2.14.3` via
  `dependencyOverrides`. Scio 0.14.4's Scala Jackson module is
  pinned to 2.14.x; transitive deps drag in jackson-databind 2.16.x
  and the runtime guard refuses to load against the mismatch.
- `BqemuContainer` is a one-line subclass that resolves Scala 2's
  `Nothing` self-type collapse on Java's `GenericContainer<SELF>`.
