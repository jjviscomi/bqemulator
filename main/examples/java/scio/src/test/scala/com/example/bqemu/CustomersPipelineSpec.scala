package com.example.bqemu

import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.time.Duration

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName

// `GenericContainer` in Java has a recursive self-type
// (`GenericContainer<SELF extends GenericContainer<SELF>>`). Scala 2's
// inferencer collapses `SELF` to `Nothing` for a bare
// `new GenericContainer(...)`, which then makes every fluent builder
// method return `Nothing` — hence the
// `value withEnv is not a member of Nothing` compile error. Define a
// concrete subclass so `SELF` resolves to a real type.
class BqemuContainer(image: DockerImageName)
    extends GenericContainer[BqemuContainer](image)

class CustomersPipelineSpec extends AnyFlatSpec with Matchers {

  // KNOWN LIMITATION (issue #17): running ``CustomersPipeline.run``
  // end-to-end against bqemulator is harder than a single flag /
  // env-var fix. v1.0.1 investigated three routes and surfaced the
  // following constraints; all three would need to land together
  // before this spec can flip to an end-to-end ``written shouldBe
  // 3L`` assertion:
  //
  // 1. **Endpoint routing.** ``--bigQueryEndpoint=http://host:port``
  //    DOES wire through to the Apiary ``Bigquery`` client's
  //    ``rootUrl`` (verified locally — auth-failure stacks confirm
  //    the override applied). But ``BIGQUERY_EMULATOR_HOST`` (which
  //    the cloud-style ``com.google.cloud.bigquery.BigQuery`` client
  //    honours) has to be set on the JVM at fork time, before any
  //    BQ class loads — sbt's ``Test / envVars`` can do this with a
  //    fixed host port, but the next two issues still bite.
  //
  // 2. **Auth.** Beam still invokes ``OAuth2Credentials.refresh()``
  //    at request time even when ``--bigQueryEndpoint`` is set, so
  //    the redirected HTTP call never fires — auth refresh against
  //    ``oauth2.googleapis.com`` 400s before the redirect happens.
  //    ``--gcpCredentialFactoryClass=...NoopCredentialFactory`` is
  //    the documented escape hatch but doesn't fully suppress the
  //    discovery chain when application-default credentials exist
  //    on the host (gcloud SDK auto-detects them past the flag).
  //
  // 3. **Batch-load path needs GCS.** ``BigQueryIO.Write`` defaults
  //    to ``BATCH_LOADS`` for bounded pipelines, which stages rows
  //    to GCS before issuing a BigQuery LOAD job. The emulator
  //    doesn't expose a GCS-compatible shim Beam can stage to;
  //    forcing ``Method.STREAMING_INSERTS`` would bypass GCS but
  //    requires changing the ``CustomersPipeline`` source and
  //    pulls in a different routing branch in BigQueryIO.
  //
  // For v1.0.1 this spec stays at the wiring-only smoke (container
  // starts, REST API is reachable, dataset creation works — the
  // bqemulator-owned part of the contract). The CustomersPipeline
  // source itself is unchanged and remains accurate documentation
  // for users running it against real BigQuery (Dataflow) or a
  // long-lived bqemulator on a stable port + a real GCS bucket.
  // Issue #17 stays open with the findings above scoped to v1.0.2+.

  "bqemulator" should "expose a working BigQuery REST surface that Scio could target" in {
    val image = sys.env.getOrElse("BQEMU_IMAGE", "ghcr.io/jjviscomi/bqemulator:dev")

    val container = new BqemuContainer(DockerImageName.parse(image))
      .withEnv("BQEMU_REST_HOST", "0.0.0.0")
      .withEnv("BQEMU_GRPC_HOST", "0.0.0.0")
      .withEnv("BQEMU_ADMIN_ENABLED", "1")
      .withExposedPorts(9050, 9060)
      .waitingFor(Wait.forHttp("/healthz").forPort(9050))

    container.start()
    try {
      val rest = s"http://${container.getHost}:${container.getMappedPort(9050)}"
      // Bound the *connect* and per-request blocking time so a stalled
      // container / NAT misconfig in CI fails fast instead of hanging
      // until the runner times out (per CodeRabbit feedback).
      //
      // Pin HTTP/1.1 explicitly — Java 17's HttpClient defaults to
      // ``HTTP_2`` and tries an h2c upgrade even on plaintext
      // ``http://``. uvicorn / h11 only speaks HTTP/1.1 and bounces
      // the negotiation with ``400 Invalid HTTP request received``,
      // which surfaces here as a baffling 400 on a perfectly valid
      // POST body.
      val client = HttpClient.newBuilder()
        .version(HttpClient.Version.HTTP_1_1)
        .connectTimeout(Duration.ofSeconds(10))
        .build()
      val timeout = Duration.ofSeconds(30)

      // 1. ``/healthz`` is reachable.
      val health = client.send(
        HttpRequest.newBuilder()
          .uri(URI.create(s"$rest/healthz"))
          .timeout(timeout)
          .GET()
          .build(),
        HttpResponse.BodyHandlers.ofString()
      )
      health.statusCode() shouldBe 200

      // 2. Dataset creation via the REST surface succeeds.
      //    Idempotent — 200/201 on first call, 409 on re-run.
      val createDs = HttpRequest.newBuilder()
        .uri(URI.create(s"$rest/bigquery/v2/projects/bqemu-demo/datasets"))
        .header("Content-Type", "application/json")
        .timeout(timeout)
        .POST(HttpRequest.BodyPublishers.ofString(
          """{"datasetReference":{"projectId":"bqemu-demo","datasetId":"scio_demo"},"location":"US"}"""
        ))
        .build()
      val createResp =
        client.send(createDs, HttpResponse.BodyHandlers.ofString())
      withClue(s"create-dataset failed: ${createResp.statusCode()} ${createResp.body()}") {
        Set(200, 201, 409) should contain(createResp.statusCode())
      }

      // 3. The dataset shows up on the listing endpoint.
      val list = client.send(
        HttpRequest.newBuilder()
          .uri(URI.create(s"$rest/bigquery/v2/projects/bqemu-demo/datasets"))
          .timeout(timeout)
          .GET()
          .build(),
        HttpResponse.BodyHandlers.ofString()
      )
      list.statusCode() shouldBe 200
      list.body() should include("scio_demo")
    } finally {
      container.stop()
    }
  }
}
