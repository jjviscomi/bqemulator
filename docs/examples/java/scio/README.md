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

## Known limitation — running Beam BigQueryIO end-to-end against bqemulator

Running ``CustomersPipeline.run`` against the emulator from inside
the test JVM hits **three independent blockers**, surfaced during
the v1.0.1 investigation
([#17](https://github.com/jjviscomi/bqemulator/issues/17)):

1. **Endpoint routing — works.** ``--bigQueryEndpoint=http://host:port``
   *does* set the Apiary ``Bigquery`` client's ``rootUrl``
   (verified locally via auth-failure stack traces confirming the
   override applied). The original v1.0.0 hypothesis that
   ``--bigQueryEndpoint`` was ignored turned out to be wrong;
   however, the next two issues still bite.
2. **Auth refresh fires before the redirected call.** With
   ``--bigQueryEndpoint`` set, Beam still invokes
   ``OAuth2Credentials.refresh()`` at request time. The refresh
   ``400``s against ``oauth2.googleapis.com`` *before* the redirected
   HTTP call ever fires.
   ``--gcpCredentialFactoryClass=org.apache.beam.sdk.extensions.gcp.auth.NoopCredentialFactory``
   is the documented escape hatch, but doesn't fully suppress the
   discovery chain when application-default credentials exist on
   the host (gcloud SDK auto-detects them past the flag).
3. **BigQueryIO.Write defaults to BATCH_LOADS — needs GCS.** Bounded
   pipelines stage rows to GCS before invoking BigQuery LOAD jobs.
   The emulator doesn't expose a GCS-compatible shim Beam can stage
   to; forcing ``Method.STREAMING_INSERTS`` would bypass GCS but
   requires changing the ``CustomersPipeline`` source and pulls in
   a different routing branch in BigQueryIO with its own quirks.

**The pipeline source itself remains correct.** Point it at real
BigQuery (Dataflow) or a long-lived bqemulator on a stable port
+ a real GCS bucket and it runs end-to-end.

Tracked for v1.0.2+. Likely path: integrate a GCS emulator
(e.g. [fsouza/fake-gcs-server](https://github.com/fsouza/fake-gcs-server))
for staging + full auth-suppression chain + decide between staying
on ``BATCH_LOADS`` (via GCS shim) or pivoting to ``STREAMING_INSERTS``.

## What the spec exercises

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
