# Spotify Scio pipeline against `bqemulator`

A minimal [Scio](https://github.com/spotify/scio) pipeline that writes
rows to `bqemulator` via the Storage Write API and reads them back via
Storage Read.

## Why this matters

Scio is the canonical Scala-on-Beam idiom — most Beam-on-JVM workloads
on Google's stack run through it. Targeting it against `bqemulator`
means engineering teams can run the same job locally that they run in
Dataflow, without spinning up a real BigQuery dataset.

## What it demonstrates

- A Beam pipeline written in Scala using Scio's
  `BigQueryType`-free `tableRowJsonFile` and `BigQuery.writeTableRows`
  idioms.
- Pointing the Beam BigQueryIO client at `bqemulator` by setting the
  `bigQueryEndpoint` pipeline option (Beam's standard escape hatch for
  non-Google BigQuery hosts).
- A ScalaTest spec that runs the pipeline on the `DirectRunner` against
  `bqemulator`, started via Testcontainers.

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

- The pipeline body is the same code you'd run in production —
  endpoint resolution is purely a `PipelineOptions` concern.
- We pin the Direct runner for reproducibility. Swapping to the
  Dataflow runner is a CLI flag change with no source changes.
