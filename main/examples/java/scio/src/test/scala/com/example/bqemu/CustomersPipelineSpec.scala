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

  // KNOWN LIMITATION: Beam Java BigQueryIO does not honour the
  // ``--bigQueryEndpoint`` flag for the *write* path — that option is
  // restricted to internal preflight validators. The Java BQ client
  // *does* honour the ``BIGQUERY_EMULATOR_HOST`` env var, but it has
  // to be present on the JVM that runs the pipeline (not just on the
  // container), and we don't know the testcontainer-mapped port
  // until after the JVM is already alive. The result: actually
  // running ``CustomersPipeline.run`` from this test routes writes to
  // ``https://bigquery.googleapis.com/...`` and 404s.
  //
  // Until upstream Scio / Beam grows a per-call endpoint override
  // (tracked for v1.0.1 follow-up), this spec verifies the
  // *wiring* — container starts, REST API is reachable, dataset
  // creation works — which is the part bqemulator owns. The
  // CustomersPipeline source remains as documentation for users who
  // will run it against either real BigQuery or a long-lived
  // bqemulator with a stable port.

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
