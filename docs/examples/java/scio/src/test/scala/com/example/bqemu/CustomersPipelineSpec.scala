package com.example.bqemu

import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.time.Duration

import com.github.dockerjava.api.model.{ExposedPort, PortBinding, Ports}
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

  // Beam Java BigQueryIO does NOT honour ``--bigQueryEndpoint`` for
  // its write path — that option is wired only into the internal
  // preflight validators, and the actual writes go through the
  // official Java ``google-cloud-bigquery`` client with the default
  // ``https://bigquery.googleapis.com/`` base URL. The Java BQ client
  // *does* honour the ``BIGQUERY_EMULATOR_HOST`` env var (it rewrites
  // the host whenever a ``BigQuery`` client is built), but JVM env
  // vars are immutable after process start — the var has to be set
  // on the JVM *before* it boots.
  //
  // The fix (issue #17): bind the testcontainer to a FIXED host port
  // and set ``BIGQUERY_EMULATOR_HOST`` via sbt's ``Test / envVars``
  // (which is applied at fork time, before the JVM loads the
  // BigQuery client classes). Both ends agree on the same port; the
  // pipeline writes route to the emulator.
  //
  // Override per-developer via ``BQEMU_TEST_HOST_PORT`` if 9099 is
  // taken on the host. ``build.sbt`` reads the same env var when
  // composing ``BIGQUERY_EMULATOR_HOST``, so changing one changes
  // both.

  private val hostPort = sys.env.getOrElse("BQEMU_TEST_HOST_PORT", "9099").toInt
  private val restBase = s"http://localhost:$hostPort"

  "CustomersPipeline" should "write 3 rows and the emulator returns them on read" in {
    val image = sys.env.getOrElse("BQEMU_IMAGE", "ghcr.io/jjviscomi/bqemulator:dev")

    val container = new BqemuContainer(DockerImageName.parse(image))
      .withEnv("BQEMU_REST_HOST", "0.0.0.0")
      .withEnv("BQEMU_GRPC_HOST", "0.0.0.0")
      .withEnv("BQEMU_ADMIN_ENABLED", "1")
      .withExposedPorts(9050, 9060)
      .waitingFor(Wait.forHttp("/healthz").forPort(9050))

    // Fixed host-port binding via the docker-java create-cmd
    // modifier. ``setPortBindings(List("9099:9050"))`` would also
    // work for REST-only, but ``withCreateContainerCmdModifier``
    // bundles both REST + gRPC bindings declaratively. Required
    // because sbt-side ``BIGQUERY_EMULATOR_HOST`` is computed at
    // JVM start — we cannot use a random testcontainer-mapped port
    // and discover it post-hoc.
    container.withCreateContainerCmdModifier({ cmd =>
      val hostConfig = cmd.getHostConfig
      hostConfig.withPortBindings(
        new PortBinding(Ports.Binding.bindPort(hostPort), new ExposedPort(9050)),
        new PortBinding(Ports.Binding.bindPort(hostPort + 10), new ExposedPort(9060))
      )
      ()
    })

    container.start()
    try {
      // Sanity-check the env var the forked JVM sees. If sbt-side
      // ``Test / envVars`` is mis-configured, the pipeline routes
      // writes to the real BigQuery URL and the test 404s with an
      // opaque ``Not Found`` deep in BQIO — fail-fast here instead.
      val emuHost = sys.env.getOrElse("BIGQUERY_EMULATOR_HOST", "")
      withClue("BIGQUERY_EMULATOR_HOST must be set by sbt's Test / envVars") {
        emuHost shouldBe s"localhost:$hostPort"
      }

      val client = HttpClient
        .newBuilder()
        .version(HttpClient.Version.HTTP_1_1)
        .connectTimeout(Duration.ofSeconds(10))
        .build()
      val timeout = Duration.ofSeconds(30)

      // Sanity check: /healthz reachable on the fixed port.
      val health = client.send(
        HttpRequest
          .newBuilder()
          .uri(URI.create(s"$restBase/healthz"))
          .timeout(timeout)
          .GET()
          .build(),
        HttpResponse.BodyHandlers.ofString()
      )
      health.statusCode() shouldBe 200

      // Pre-create the dataset so CREATE_IF_NEEDED on the table
      // doesn't have to also handle dataset creation under one
      // BQIO write call.
      val createDs = HttpRequest
        .newBuilder()
        .uri(URI.create(s"$restBase/bigquery/v2/projects/bqemu-demo/datasets"))
        .header("Content-Type", "application/json")
        .timeout(timeout)
        .POST(
          HttpRequest.BodyPublishers.ofString(
            """{"datasetReference":{"projectId":"bqemu-demo","datasetId":"scio_demo"},"location":"US"}"""
          )
        )
        .build()
      val createDsResp =
        client.send(createDs, HttpResponse.BodyHandlers.ofString())
      withClue(s"create-dataset failed: ${createDsResp.statusCode()} ${createDsResp.body()}") {
        Set(200, 201, 409) should contain(createDsResp.statusCode())
      }

      // Run the pipeline end-to-end. Beam DirectRunner; the
      // ``BIGQUERY_EMULATOR_HOST`` env var on the forked JVM
      // (applied by sbt's ``Test / envVars``) routes BQ client
      // writes to the emulator.
      val written = CustomersPipeline.run(
        Array(
          "--runner=DirectRunner",
          "--project=bqemu-demo",
          "--bqProject=bqemu-demo",
          "--bqDataset=scio_demo"
        )
      )
      written shouldBe 3L

      // Read the rows back via the emulator REST + jobs.query and
      // confirm BQIO actually wrote the 3 customers.
      val countQuery = HttpRequest
        .newBuilder()
        .uri(URI.create(s"$restBase/bigquery/v2/projects/bqemu-demo/queries"))
        .header("Content-Type", "application/json")
        .timeout(timeout)
        .POST(
          HttpRequest.BodyPublishers.ofString(
            """{"query":"SELECT COUNT(*) AS n FROM `bqemu-demo`.`scio_demo`.`customers`","useLegacySql":false}"""
          )
        )
        .build()
      val countResp = client.send(countQuery, HttpResponse.BodyHandlers.ofString())
      withClue(s"count-query failed: ${countResp.statusCode()} ${countResp.body()}") {
        countResp.statusCode() shouldBe 200
      }
      // Cheap structural check — the JSON body has ``"f": [{"v":"3"}]``
      // for a single-row, single-column result. Skipping a JSON parser
      // here keeps the spec free of an extra test-only dep; if the
      // shape changes the substring assertion fails loudly.
      countResp.body() should include("\"3\"")
    } finally {
      container.stop()
    }
  }
}
