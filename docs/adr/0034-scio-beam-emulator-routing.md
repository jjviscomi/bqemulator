# ADR 0034: Scio + Beam BigQueryIO routing against bqemulator (issue #17)

- **Status**: Accepted

## Context

[Issue #17](https://github.com/jjviscomi/bqemulator/issues/17) — closing
the v1.0.0 "scio test exercises wiring only" caveat — turned out to be
harder than the original framing suggested. The v1.0.1 investigation
([CHANGELOG.md](https://github.com/jjviscomi/bqemulator/blob/main/CHANGELOG.md) `## [1.0.1]` "Changed" block and
``docs/examples/java/scio/src/test/scala/com/example/bqemu/CustomersPipelineSpec.scala``)
surfaced three independent blockers that all had to land together
before ``CustomersPipeline.run(...)`` could drive end-to-end against a
local emulator:

| Blocker | Beam 2.55.1 source-of-truth |
|---|---|
| **1. Endpoint routing.** No ``--bigQueryEndpoint`` pipeline option exists in the Java SDK. The Go SDK adds emulator support via the ``BIGQUERY_EMULATOR_HOST`` env var ([apache/beam#34037](https://github.com/apache/beam/pull/34037)); the Java side never adopted it. ``BigQueryServicesImpl.newBigQueryClient`` (Beam ``sdks/java/io/google-cloud-platform/.../BigQueryServicesImpl.java`` line 1550) constructs the Apiary ``Bigquery`` client with the default ``rootUrl = https://bigquery.googleapis.com/`` — no override hook. | [BigQueryServicesImpl.java#L1550](https://github.com/apache/beam/blob/v2.55.1/sdks/java/io/google-cloud-platform/src/main/java/org/apache/beam/sdk/io/gcp/bigquery/BigQueryServicesImpl.java#L1550) |
| **2. Auth.** With unredirected traffic, Beam's ``HttpCredentialsAdapter`` invokes ``OAuth2Credentials.refresh()`` at request time. On developer laptops with a stale ``application_default_credentials.json``, the refresh ``400``s against ``oauth2.googleapis.com`` *before* any redirected HTTP call fires. | [GcpOptions.java#GcpUserCredentialsFactory](https://github.com/apache/beam/blob/v2.55.1/sdks/java/extensions/google-cloud-platform-core/src/main/java/org/apache/beam/sdk/extensions/gcp/options/GcpOptions.java) |
| **3. BATCH_LOADS needs GCS.** ``BigQueryIO.Write`` defaults to ``Method.DEFAULT`` → ``BATCH_LOADS`` for bounded pipelines, which stages rows to ``gs://<gcpTempLocation>/BigQueryWriteTemp/...`` before issuing a BigQuery LOAD job. The emulator's ``BQEMU_GCS_LOCAL_ROOT`` shim (ADR 0027 / G1) resolves ``gs://`` URIs to a local filesystem path but does not implement the GCS HTTP/JSON API that Beam writes to. | [BatchLoads.java](https://github.com/apache/beam/blob/v2.55.1/sdks/java/io/google-cloud-platform/src/main/java/org/apache/beam/sdk/io/gcp/bigquery/BatchLoads.java) |

The v1.0.1 release shipped with the spec exercising wiring-only smoke
(container up, REST reachable, dataset-creation works) and tracked the
end-to-end assertion under #17 for v1.0.2.

## Decision

Close issue #17 in **v1.0.2** by changing **only the scio example** —
no emulator-side code change is needed.

### 1. ``CustomersPipeline.scala`` accepts an optional ``--bqEmulatorEndpoint``

Production users (real BigQuery / Dataflow) don't set the flag and
take the existing ``rows.saveAsBigQueryTable(...)`` scio idiom
unchanged. The example/CI driver sets ``--bqEmulatorEndpoint=<rest>``
to switch the same pipeline into the test-services branch.

When the flag IS set, the pipeline drops down to raw
``BigQueryIO.writeTableRows()`` so it can attach
``.withTestServices(EmulatorBigQueryServices(endpoint))``. Scio's
``saveAsBigQueryTable`` wraps the same ``BigQueryIO.Write`` transform
but does not expose the ``withTestServices`` hook; the test-services
attachment requires constructing the Write directly.

### 2. ``EmulatorBigQueryServices`` extends ``BigQueryServicesImpl``

The class lives in ``org.apache.beam.sdk.io.gcp.bigquery`` (Beam's
package — same package different JAR, which Java permits) so it can
reach the ``@VisibleForTesting`` constructors on
``BigQueryServicesImpl.JobServiceImpl`` /
``BigQueryServicesImpl.DatasetServiceImpl``. Both accept a pre-built
Apiary ``Bigquery`` client; ``EmulatorBigQueryServices`` builds one
with ``setRootUrl(emulator)`` and a no-op ``HttpRequestInitializer``,
then hands it to those constructors.

The override is the smallest possible scope — the Job/Dataset service
*bodies* (``startLoadJob``, ``pollJob``, ``getDataset``, etc.) are
inherited from Beam's default implementations and exercise the
upstream code paths unchanged.

### 3. A ``fake-gcs-server`` sidecar provides GCS staging

The scio spec brings up
[``fsouza/fake-gcs-server``](https://github.com/fsouza/fake-gcs-server)
v1.54.0 alongside the bqemulator container, on a shared testcontainers
``Network``. Beam stages the BATCH_LOADS shards to
``gs://bqemu-staging/...`` via ``--gcsEndpoint=http://<fake-gcs>/storage/v1/``
(Beam ``Transport.newStorageClient`` honours ``--gcsEndpoint`` —
verified at v2.55.1 line ~108–115 — calling
``storageBuilder.setRootUrl(...)`` when the option is non-null).

### 4. fake-gcs-server's filesystem backend is bind-mounted into bqemulator

fake-gcs-server's filesystem backend stores objects at
``{rootDir}/{bucket}/{object_name}``
([fs.go](https://github.com/fsouza/fake-gcs-server/blob/v1.54.0/internal/backend/fs.go))
— byte-exact with bqemulator's ``_resolve_uri`` which maps
``gs://{bucket}/{object_name}`` to
``{BQEMU_GCS_LOCAL_ROOT}/{bucket}/{object_name}``
([executor.py:1103](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)).
The scio spec bind-mounts a single host directory into both
containers at their respective roots:

```text
fake-gcs-server -filesystem-root /data  ┐
                                         ├── /tmp/bqemu-gcs-staging-{rand}
bqemulator BQEMU_GCS_LOCAL_ROOT=/var/lib/bqemu-gcs ┘
```

Beam stages a shard via fake-gcs-server's HTTP API; fake-gcs-server
materialises the object at ``/data/bqemu-staging/.../shard.json`` on
disk; bqemulator's LOAD-job executor opens the same byte path under
``/var/lib/bqemu-gcs/bqemu-staging/.../shard.json`` via DuckDB's
``COPY ... FROM '...' (FORMAT JSON)``. No emulator-side fetch over
the network, no new code path in ``executor.py``.

### 5. Auth suppression via ``NoopCredentialFactory``

The spec passes
``--gcpCredentialFactoryClass=org.apache.beam.sdk.extensions.gcp.auth.NoopCredentialFactory``.
Beam's ``GcpUserCredentialsFactory.create`` honours the factory class
via ``InstanceBuilder.ofType(CredentialFactory.class).fromClass(
gcpOptions.getCredentialFactoryClass())`` — calling
``NoopCredentialFactory.getCredential()`` returns inert
``NoopCredentials`` whose ``getRequestMetadata`` returns ``null`` and
``refresh()`` is a no-op. No oauth2 refresh fires, no
``oauth2.googleapis.com`` traffic.

A belt-and-suspenders ``Test / envVars`` block in the scio example's
``build.sbt`` sets ``CLOUDSDK_CONFIG`` to a fresh-empty temp dir and
``GOOGLE_APPLICATION_CREDENTIALS`` to a deliberately-missing path —
this keeps the no-op-auth contract honest on developer laptops that
have ``gcloud auth application-default login`` state laying around,
even though ``NoopCredentialFactory`` alone is sufficient against the
source code.

## Rationale

### Why this approach over alternatives

Several alternatives were rejected:

1. **Add ``--bigQueryEndpoint`` to Beam.** Upstream PR that the Java
   side would need to land. Multi-week timeline at best; downstream
   users would still wait for the next Beam release. Reject — out of
   scope for v1.0.2 and disenfranchises every Scio + bqemulator user
   who can't wait.
2. **JVM-level HTTP proxy with URL rewriting.** Would require a
   TLS-terminating proxy with a custom CA, ``/etc/hosts`` hijacking
   of ``bigquery.googleapis.com``, and a system-property cascade.
   Brittle, intrusive, and impossible to ship as part of a clean
   ScalaTest spec.
3. **``Method.STREAMING_INSERTS`` instead of ``BATCH_LOADS``.** Avoids
   GCS staging entirely but routes through ``tabledata.insertAll``,
   which has its own behavioural quirks (no atomic write disposition,
   per-row size limits, no schema evolution). Production demos prefer
   BATCH_LOADS; switching the example's default to
   STREAMING_INSERTS would mis-represent the canonical Scio +
   BigQueryIO pattern.
4. **Inline GCS shim inside the bqemulator container.** Would expand
   the emulator's scope from "BigQuery" to "BigQuery + GCS". Real
   BigQuery does not include GCS; the emulator's charter follows the
   real service's surface, not the surface of upstream callers'
   staging. Out of scope per
   [out-of-scope.md](../reference/out-of-scope.md).
5. **Document the limitation and re-defer.** v1.0.1 already deferred
   it once; "wiring-only smoke" is a real gap in a production-stable
   release. The "no deferral" principle in AGENTS.md says scope
   boundaries must be cleanly excluded, not silently parked.

The test-services approach above wins because:

- The pipeline source stays production-shaped — a single ``optional``
  branch with the production path unchanged.
- The emulator source stays untouched — no new endpoints, no new
  config knob, no new attack surface.
- The fake-gcs-server sidecar is a 30-line addition in the scio spec,
  fully contained in ``docs/examples/java/scio/``.
- The end-to-end test exercises Beam's BATCH_LOADS path that real
  Dataflow users hit — not a synthetic streaming-inserts substitute.

### Why ``EmulatorBigQueryServices`` lives in Beam's package

The constructors
``BigQueryServicesImpl.JobServiceImpl(Bigquery client)`` and
``BigQueryServicesImpl.DatasetServiceImpl(Bigquery client,
PipelineOptions options)`` are marked ``@VisibleForTesting`` and are
package-private. Java's access scoping is by *package*, not by *JAR*;
a class declared in ``org.apache.beam.sdk.io.gcp.bigquery`` in our
example's jar can reach those constructors at compile time and
runtime. The Beam community uses the same pattern in its own test
suite ([``BigQueryServicesImplTest``](https://github.com/apache/beam/blob/v2.55.1/sdks/java/io/google-cloud-platform/src/test/java/org/apache/beam/sdk/io/gcp/bigquery/BigQueryServicesImplTest.java)),
so split-package against Beam is an established idiom rather than a
hack.

### Why ``fake-gcs-server`` and not a custom GCS shim

The alternative of writing our own GCS HTTP/JSON-API mock was
considered and rejected on the same scope ground as item 4 above —
plus fake-gcs-server is a 4 MB Docker image with five years of
production use across the Go testing ecosystem. Building our own
would duplicate that mature surface for no marginal gain.

### Why bind-mount and not HTTP fetch

bqemulator already has a filesystem ``gs://`` resolver
([``_resolve_uri``](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)).
The fake-gcs-server filesystem-backend layout is *byte-identical* to
what ``_resolve_uri`` expects. Bind-mounting the same host directory
into both containers lets us route Beam's GCS writes through
fake-gcs-server (which materialises the bytes on disk) and bqemulator's
LOAD reads through the existing filesystem path — no new code, no
HTTP fetch boundary inside the executor, no double-network hop. The
metadata sidecars fake-gcs-server writes are ``.metadata``-suffixed
files that DuckDB's ``COPY ... FROM 'path/to/data.json'`` does not
accidentally include because the LOAD job's ``sourceUris`` reference
the data file by name, not by glob.

## Consequences

### Positive

- Issue #17 closes cleanly — the scio example demonstrates a real
  end-to-end BATCH_LOADS round-trip against bqemulator: 3 rows
  written, 3 rows read back via ``jobs.query``.
- The production demo value of the example is preserved — running
  ``CustomersPipeline`` against real Dataflow uses the same source
  code, same Scio idiom, no test-mode wiring.
- ``executor.py`` stays at the v1.0.1 line count; the existing
  ``BQEMU_GCS_LOCAL_ROOT`` shim absorbs the new use case without
  modification.
- The pattern documented here generalises: any Beam Java pipeline
  that uses ``BigQueryIO.Write`` can apply the same
  ``EmulatorBigQueryServices`` + fake-gcs-server recipe to test
  against bqemulator without modifying their pipeline source. The
  example doubles as a reference recipe.

### Negative

- The scio example now requires Docker to bring up a second container
  (fake-gcs-server) for the end-to-end test. The wiring-only smoke
  path is gone; CI runtime for the scio job goes from ~12s to ~20s
  (still well below the 5-minute Examples-workflow ceiling).
- ``CustomersPipeline.scala`` carries a conditional branch for the
  test-services injection. The branch is small (15 lines) and the
  production path stays identical, but the source is no longer
  100% scio-idiomatic when the flag is set. Acceptable — the comment
  block at the top of the file calls this out, and the trade-off
  (a single conditional vs. a separate test-only pipeline file) is
  the smaller one for users reading the example.
- ``EmulatorBigQueryServices`` lives in ``org.apache.beam.sdk.io.gcp.bigquery``
  (split-package). If Beam ever modularises ``google-cloud-platform``
  with a ``module-info.java`` that closes the package, the class
  would have to move to a runtime-reflection / proxy implementation.
  Not a near-term risk (Beam has no module-info on this jar as of
  v2.55.1), but worth re-checking on every Beam major-version bump.

### Neutral

- No new third-party dependencies in the bqemulator side. The
  example pulls ``fsouza/fake-gcs-server:1.54.0`` as a runtime Docker
  image, not a Maven artifact.
- The pattern doesn't generalise to a Python SDK against bqemulator
  — Beam's Python ``BigQueryIO`` uses a different services
  abstraction. A separate ADR will document the Python-side recipe
  when the equivalent issue (currently tracked at the
  pyspark-bigquery example's wiring-only baseline) is closed.

## References

- [Issue #17](https://github.com/jjviscomi/bqemulator/issues/17) — the
  consumer-side gap that prompted this work.
- [apache/beam#34037](https://github.com/apache/beam/pull/34037) —
  the Go SDK's ``BIGQUERY_EMULATOR_HOST`` adoption, for context on
  why Java requires a custom hook.
- [Beam BigQueryServicesImpl v2.55.1](https://github.com/apache/beam/blob/v2.55.1/sdks/java/io/google-cloud-platform/src/main/java/org/apache/beam/sdk/io/gcp/bigquery/BigQueryServicesImpl.java)
  — the ``@VisibleForTesting`` constructors this ADR depends on.
- [Beam Transport v2.55.1](https://github.com/apache/beam/blob/v2.55.1/sdks/java/extensions/google-cloud-platform-core/src/main/java/org/apache/beam/sdk/extensions/gcp/util/Transport.java)
  — proves ``--gcsEndpoint`` is honoured by
  ``newStorageClient``.
- [fake-gcs-server v1.54.0 fs backend](https://github.com/fsouza/fake-gcs-server/blob/v1.54.0/internal/backend/fs.go)
  — proves filesystem-backend layout matches bqemulator's
  ``_resolve_uri``.
- [ADR 0027](0027-load-extract-avro-orc.md) — sibling decision for
  the original ``BQEMU_GCS_LOCAL_ROOT`` shim that this ADR reuses.
- [ADR 0033](0033-storage-read-arrow-ipc-bare-message-contract.md) —
  the v1.0.1 ADR; same shape and template followed here.
