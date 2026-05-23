# Spotify Scio pipeline against `bqemulator`

A minimal [Scio](https://github.com/spotify/scio) pipeline that
writes a customers table to BigQuery and reads it back. The Scala
source under `src/main/scala/` is production-shaped — what you'd run
against real BigQuery or Dataflow today, with a single optional flag
for the emulator-test path.

## Why this matters

Scio is the canonical Scala-on-Beam idiom — most Beam-on-JVM workloads
on Google's stack run through it. Targeting it against `bqemulator`
means engineering teams can run the same job locally that they run in
Dataflow, without spinning up a real BigQuery dataset.

## How the pipeline runs end-to-end against bqemulator (v1.0.2+)

Beam 2.55.1's Java SDK has no built-in `BIGQUERY_EMULATOR_HOST`
support (the Go SDK does — see
[apache/beam#34037](https://github.com/apache/beam/pull/34037) — but
the Java side never adopted it). v1.0.2 closes
[#17](https://github.com/jjviscomi/bqemulator/issues/17) by wiring a
test-services hook directly into the scio example, with no
bqemulator-side code change:

1. **`CustomersPipeline.run` accepts an optional `--bqEmulatorEndpoint`.**
   Production callers (real BigQuery / Dataflow) don't pass the flag
   and take scio's idiomatic `saveAsBigQueryTable(...)` branch
   unchanged. When the flag IS present, the pipeline drops to raw
   `BigQueryIO.writeTableRows().withTestServices(
   EmulatorBigQueryServices(endpoint))` for the BQ-traffic redirect.
2. **`EmulatorBigQueryServices`** (in this example, at
   `src/main/scala/org/apache/beam/sdk/io/gcp/bigquery/`) extends
   Beam's `BigQueryServicesImpl` and overrides `getJobService` /
   `getDatasetService` to construct the Apiary `Bigquery` client with
   `setRootUrl(emulator)`. The class lives in Beam's package so it
   can reach the `@VisibleForTesting` constructors on
   `JobServiceImpl` / `DatasetServiceImpl` that accept a pre-built
   client.
3. **A `fake-gcs-server` sidecar** ([fsouza/fake-gcs-server](https://github.com/fsouza/fake-gcs-server))
   handles GCS staging for Beam's default `BATCH_LOADS` write method.
   The spec brings both containers up on a shared testcontainers
   network and bind-mounts a single host directory into both at their
   respective storage roots — so files Beam stages to
   `gs://bqemu-staging/...` materialise on disk where bqemulator's
   existing `BQEMU_GCS_LOCAL_ROOT` shim resolves them.
4. **Auth suppression via Beam's `NoopCredentialFactory`** —
   `--gcpCredentialFactoryClass=org.apache.beam.sdk.extensions.gcp.auth.NoopCredentialFactory`
   short-circuits `OAuth2Credentials.refresh()` so no token grant
   fires against `oauth2.googleapis.com`. The example's `build.sbt`
   sets `CLOUDSDK_CONFIG` and `GOOGLE_APPLICATION_CREDENTIALS` to
   empty values as belt-and-suspenders defence on developer laptops
   with stale `gcloud auth application-default login` state.

See [ADR 0034](../../../adr/0034-scio-beam-emulator-routing.md) for the
full design — alternatives considered, source-of-truth references in
Beam, and consequences.

## What the spec exercises

The ScalaTest spec
([`CustomersPipelineSpec`](src/test/scala/com/example/bqemu/CustomersPipelineSpec.scala))
drives the real `CustomersPipeline.run` and asserts:

- bqemulator + fake-gcs-server both start.
- Dataset creation via the REST surface succeeds (idempotent —
  `{200, 201, 409}`).
- `CustomersPipeline.run` returns 3 (the rows it intended to write).
- A REST `SELECT COUNT(*)` against
  `\`bqemu-demo\`.scio_demo.customers` returns 3 — confirming the
  LOAD job actually landed the rows on disk.

The pipeline source itself stays production-ready: pointed at real
BigQuery (Dataflow) with no `--bqEmulatorEndpoint`, the same code
takes the scio path and writes via the real GCS staging bucket.

## Layout

```text
build.sbt                                             — sbt build definition
project/build.properties                              — sbt version pin
src/main/scala/com/example/bqemu/CustomersPipeline.scala
src/main/scala/org/apache/beam/sdk/io/gcp/bigquery/EmulatorBigQueryServices.scala
src/test/scala/com/example/bqemu/CustomersPipelineSpec.scala
```

## Run

```bash
make test
```

`make test` runs `sbt test`. Requires Docker (for Testcontainers to
spin up the bqemulator + fake-gcs-server pair).

## What to look for

- `build.sbt` holds the Jackson stack at `2.14.3` via
  `dependencyOverrides`. Scio 0.14.4's Scala Jackson module is
  pinned to 2.14.x; transitive deps drag in jackson-databind 2.16.x
  and the runtime guard refuses to load against the mismatch.
- `testcontainers` is at 1.21.4 — Docker 29+ rejects docker-java
  clients announcing API < 1.40, and the 1.20.x line still ships an
  older shaded docker-java that trips that check on modern Docker
  Desktop. Bumping to 1.21.x fixes it.
- The `EmulatorBigQueryServices` Scala file lives in Beam's package
  (`org.apache.beam.sdk.io.gcp.bigquery`) — Java's package scoping
  is by *package*, not *JAR*, so the split-package access compiles
  and runs cleanly. This is the same idiom Beam's own test suite
  uses for the `BigQueryServicesImpl` subclasses.
- `BqemuContainer` and `FakeGcsContainer` are one-line subclasses
  that resolve Scala 2's `Nothing` self-type collapse on Java's
  `GenericContainer<SELF>`.
- The spec's `runPipelineOrDumpDetail` wrapper exists because Beam's
  BATCH_LOADS `finishBundle` collapses every per-writer close
  failure into a single `IOException("Failed to close some
  writers")` and stashes the real causes as suppressed exceptions —
  the wrapper prints the full chain so a debug session starting
  from a green test failure doesn't lose the underlying GCS error.

## Adapting this to your own pipeline

The `EmulatorBigQueryServices` class and the fake-gcs-server sidecar
recipe both generalise. Copy the file under your own
`src/main/scala/org/apache/beam/sdk/io/gcp/bigquery/` and call
`BigQueryIO.Write.withTestServices(new
EmulatorBigQueryServices(emulatorEndpoint))` from your test-mode
branch. The sidecar pattern (shared bind mount + `--gcsEndpoint=...storage/v1/`)
is documented in the spec.
