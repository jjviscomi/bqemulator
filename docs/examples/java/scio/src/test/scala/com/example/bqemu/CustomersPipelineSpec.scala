package com.example.bqemu

import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.nio.file.{Files, Path, Paths}
import java.time.Duration

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.testcontainers.containers.{BindMode, GenericContainer, Network}
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName

// `GenericContainer` in Java has a recursive self-type
// (`GenericContainer<SELF extends GenericContainer<SELF>>`). Scala 2's
// inferencer collapses `SELF` to `Nothing` for a bare
// `new GenericContainer(...)`, which then makes every fluent builder
// method return `Nothing` — hence the
// `value withEnv is not a member of Nothing` compile error. Define a
// concrete subclass per image so `SELF` resolves to a real type.
class BqemuContainer(image: DockerImageName)
    extends GenericContainer[BqemuContainer](image)
class FakeGcsContainer(image: DockerImageName)
    extends GenericContainer[FakeGcsContainer](image)

class CustomersPipelineSpec extends AnyFlatSpec with Matchers {

  // End-to-end: drive the actual ``CustomersPipeline.run`` against a
  // ``bqemulator`` container PLUS a ``fake-gcs-server`` sidecar that
  // implements GCS staging for Beam BigQueryIO's default ``BATCH_LOADS``
  // path. Closes issue #17 — the three blockers identified during the
  // v1.0.1 investigation (endpoint routing, auth-refresh, GCS staging)
  // are all addressed by the sidecar + pipeline-options combination
  // below. ADR 0034 documents the decision; the example README has the
  // user-facing prose.
  //
  // Mechanism (no bqemulator-side code change):
  //
  // - ``--bqEmulatorEndpoint=<emu>`` is consumed by
  //   ``CustomersPipeline.run`` (not by Beam) and causes the pipeline
  //   to switch from scio's ``saveAsBigQueryTable`` to raw
  //   ``BigQueryIO.writeTableRows().withTestServices(
  //   EmulatorBigQueryServices(endpoint))``. The
  //   ``EmulatorBigQueryServices`` class lives in the
  //   ``org.apache.beam.sdk.io.gcp.bigquery`` package so it can reach
  //   the ``@VisibleForTesting`` constructors on
  //   ``BigQueryServicesImpl.JobServiceImpl`` /
  //   ``DatasetServiceImpl`` that accept a pre-built Apiary
  //   ``Bigquery`` client with ``setRootUrl(emulator)``. Beam 2.55.1's
  //   Java SDK has no built-in ``BIGQUERY_EMULATOR_HOST`` (only the Go
  //   SDK does, per apache/beam#34037), so the test-services hook is
  //   the canonical Java-side path.
  // - ``--gcsEndpoint=<fake-gcs>`` redirects Beam's GcsUtil Storage
  //   client at the fake-gcs-server sidecar (verified in Beam 2.55.1's
  //   ``Transport.newStorageClient``, which calls
  //   ``storageBuilder.setRootUrl(...)`` when ``getGcsEndpoint`` is
  //   non-null).
  // - ``--gcpCredentialFactoryClass=NoopCredentialFactory`` returns
  //   inert ``NoopCredentials`` whose ``getRequestMetadata`` returns
  //   ``null`` and ``refresh()`` is a no-op, so no oauth2 refresh ever
  //   fires (verified in Beam 2.55.1's ``GcpUserCredentialsFactory.create``
  //   honouring the factory-class option via ``InstanceBuilder``).
  // - The bqemulator's existing ``BQEMU_GCS_LOCAL_ROOT`` shim maps
  //   ``gs://bucket/path`` to a filesystem path under
  //   ``/var/lib/bqemu-gcs`` (G1 / ADR 0027). We bind-mount the same
  //   host directory into the fake-gcs-server's ``-filesystem-root``,
  //   so the LOAD-job source URIs that Beam stages resolve to the
  //   exact same files on disk.
  //
  // The pipeline source under ``src/main/scala/`` is unchanged — what
  // the user runs against real Dataflow is what runs here.
  "CustomersPipeline" should
    "write 3 rows via BATCH_LOADS and the emulator returns them on read" in {
    val bqemuImage =
      sys.env.getOrElse("BQEMU_IMAGE", "ghcr.io/jjviscomi/bqemulator:dev")
    val fakeGcsImage = "fsouza/fake-gcs-server:1.54.0"

    // Shared host directory bound into BOTH containers — fake-gcs-server
    // writes object bytes to its ``-filesystem-root`` at
    // ``{root}/{bucket}/{object_name}``; bqemulator's
    // ``_resolve_uri`` maps ``gs://{bucket}/{object_name}`` to
    // ``{BQEMU_GCS_LOCAL_ROOT}/{bucket}/{object_name}``. Pointing both
    // at the same physical directory means LOAD-job source URIs
    // resolve to the bytes Beam staged, without an extra HTTP fetch on
    // the bqemulator side.
    //
    // ``/tmp`` (rather than ``/var/folders/...``) keeps macOS Docker
    // Desktop's bind mounts on the fast path.
    val sharedDir: Path =
      Files.createTempDirectory(Paths.get("/tmp"), "bqemu-gcs-staging")
    sharedDir.toFile.setReadable(true, false)
    sharedDir.toFile.setWritable(true, false)
    sharedDir.toFile.setExecutable(true, false)

    val stagingBucket = "bqemu-staging"
    // Pre-create the bucket directory so fake-gcs-server picks it up
    // on boot (its ``-filesystem-root`` scanner enumerates immediate
    // subdirectories as buckets at startup). Beam's GcsUtil tolerates
    // a pre-existing bucket and just stages objects under it.
    val stagingDir = sharedDir.resolve(stagingBucket)
    Files.createDirectories(stagingDir)
    stagingDir.toFile.setReadable(true, false)
    stagingDir.toFile.setWritable(true, false)
    stagingDir.toFile.setExecutable(true, false)

    val net = Network.newNetwork()

    val fakeGcs = new FakeGcsContainer(DockerImageName.parse(fakeGcsImage))
      .withNetwork(net)
      .withNetworkAliases("fake-gcs")
      .withCommand(
        "-scheme",
        "http",
        "-port",
        "4443",
        "-host",
        "0.0.0.0",
        "-filesystem-root",
        "/data",
      )
      .withFileSystemBind(sharedDir.toString, "/data", BindMode.READ_WRITE)
      // Run fake-gcs-server as the same UID/GID as the bqemulator
      // image's runtime user (``bqemu`` = 1000:1000). fake-gcs-server's
      // ``writeFile(path, buf, 0o600)`` mode-bits are owner-only; on
      // Linux CI runners the bind-mounted file lands on the host with
      // the writer's UID and the bqemulator container (UID 1000) can't
      // read a UID-0 0600 file. macOS Docker Desktop translates UIDs
      // transparently and would silently let this slide. Pinning the
      // fake-gcs-server UID closes the divergence.
      .withCreateContainerCmdModifier {
        cmd: com.github.dockerjava.api.command.CreateContainerCmd =>
          cmd.withUser("1000:1000"); ()
      }
      .withExposedPorts(4443)
      .waitingFor(Wait.forListeningPort())

    val bqemu = new BqemuContainer(DockerImageName.parse(bqemuImage))
      .withNetwork(net)
      .withNetworkAliases("bqemu")
      .withEnv("BQEMU_REST_HOST", "0.0.0.0")
      .withEnv("BQEMU_GRPC_HOST", "0.0.0.0")
      .withEnv("BQEMU_ADMIN_ENABLED", "1")
      .withEnv("BQEMU_GCS_LOCAL_ROOT", "/var/lib/bqemu-gcs")
      .withFileSystemBind(
        sharedDir.toString,
        "/var/lib/bqemu-gcs",
        BindMode.READ_WRITE,
      )
      .withExposedPorts(9050, 9060)
      .waitingFor(Wait.forHttp("/healthz").forPort(9050))

    fakeGcs.start()
    bqemu.start()
    try {
      val rest =
        s"http://${bqemu.getHost}:${bqemu.getMappedPort(9050)}"
      // Beam's ``Transport.newStorageClient`` splits the endpoint URL
      // into ``rootUrl`` (protocol + host + port) and ``servicePath``
      // (URL path). Without the ``/storage/v1/`` path component
      // Beam emits upload URLs of the shape
      // ``http://host/upload/b/{bucket}/o`` (missing the
      // ``storage/v1/`` middle), which fake-gcs-server rejects with
      // 404. With the path the URLs come out as
      // ``http://host/upload/storage/v1/b/{bucket}/o`` — the canonical
      // GCS JSON-API upload prefix that fake-gcs-server implements.
      val gcs =
        s"http://${fakeGcs.getHost}:${fakeGcs.getMappedPort(4443)}/storage/v1/"

      // Pin HTTP/1.1 — Java 17's default HTTP_2 tries h2c upgrade on
      // plaintext URLs, which uvicorn / h11 bounce with 400.
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
        HttpResponse.BodyHandlers.ofString(),
      )
      health.statusCode() shouldBe 200

      // 2. Dataset creation via the REST surface — BigQuery (and
      //    therefore the LOAD-job dispatch below) requires the
      //    parent dataset to exist; CREATE_IF_NEEDED on the write
      //    only auto-creates the TABLE, not the dataset.
      val createDs = HttpRequest.newBuilder()
        .uri(URI.create(
          s"$rest/bigquery/v2/projects/bqemu-demo/datasets",
        ))
        .header("Content-Type", "application/json")
        .timeout(timeout)
        .POST(HttpRequest.BodyPublishers.ofString(
          """{"datasetReference":{"projectId":"bqemu-demo","datasetId":"scio_demo"},"location":"US"}""",
        ))
        .build()
      val createResp =
        client.send(createDs, HttpResponse.BodyHandlers.ofString())
      withClue(
        s"create-dataset failed: ${createResp.statusCode()} ${createResp.body()}",
      ) {
        Set(200, 201, 409) should contain(createResp.statusCode())
      }

      // 3. Drive the actual pipeline. The arguments below cover the
      //    three blockers from the #17 investigation:
      //    - ``--bqEmulatorEndpoint`` is pipeline-private (consumed
      //      by ``CustomersPipeline.run``, not Beam) and causes the
      //      pipeline to inject ``EmulatorBigQueryServices`` via
      //      ``withTestServices(...)``, redirecting the Apiary
      //      ``Bigquery`` client at the emulator's REST surface.
      //      Beam 2.55.1's Java SDK has no built-in
      //      ``BIGQUERY_EMULATOR_HOST`` support — only the Go SDK
      //      does (apache/beam#34037), so the test-services hook is
      //      the canonical Java-side path.
      //    - ``--gcsEndpoint`` redirects Beam's GcsUtil Storage
      //      client at the fake-gcs-server sidecar (verified in
      //      Beam 2.55.1's ``Transport.newStorageClient``).
      //    - ``--gcpCredentialFactoryClass=NoopCredentialFactory``
      //      returns inert ``NoopCredentials`` whose ``refresh()``
      //      is a no-op, suppressing the OAuth2 refresh path that
      //      would otherwise 400 against ``oauth2.googleapis.com``
      //      on hosts with stale ADC.
      // Diagnostic wrapper — Beam's BatchLoads ``finishBundle``
      // collapses every per-writer close failure into a single
      // ``IOException("Failed to close some writers")`` and stashes
      // the real causes as suppressed exceptions. Without this dump
      // ScalaTest only shows the top-level message and the actual GCS
      // upload error stays invisible.
      def runPipelineOrDumpDetail(args: Array[String]): Long = {
        try CustomersPipeline.run(args)
        catch {
          case e: Throwable =>
            def printChain(t: Throwable, depth: Int): Unit = {
              val indent = "  " * depth
              System.err.println(s"$indent${t.getClass.getName}: ${t.getMessage}")
              t.getStackTrace.take(5).foreach(s =>
                System.err.println(s"$indent  at $s"),
              )
              Option(t.getCause).foreach { c =>
                System.err.println(s"${indent}Caused by:")
                printChain(c, depth + 1)
              }
              t.getSuppressed.foreach { s =>
                System.err.println(s"${indent}Suppressed:")
                printChain(s, depth + 1)
              }
            }
            System.err.println("=== PIPELINE FAILURE CHAIN ===")
            printChain(e, 0)
            System.err.println("=== END PIPELINE FAILURE CHAIN ===")
            throw e
        }
      }
      val written = runPipelineOrDumpDetail(Array(
        "--runner=DirectRunner",
        "--project=bqemu-demo",
        // Scio's DirectRunner default ``tempLocation`` is a host-
        // local ``scio-temp-*`` directory under ``java.io.tmpdir``,
        // which would route the BatchLoads file staging through the
        // host filesystem and produce a LOAD-job ``sourceUris`` that
        // bqemulator can't resolve against ``BQEMU_GCS_LOCAL_ROOT``.
        // Setting BOTH ``--tempLocation`` (the Beam-wide default)
        // and ``--gcpTempLocation`` (the GCP-specific override) is
        // belt-and-suspenders: scio's ScioContext prefers
        // ``tempLocation`` for its own bookkeeping, while
        // BigQueryIO's BatchLoads reads ``gcpTempLocation`` for the
        // staging bucket path.
        s"--tempLocation=gs://$stagingBucket/",
        s"--gcpTempLocation=gs://$stagingBucket/",
        s"--gcsEndpoint=$gcs",
        "--gcpCredentialFactoryClass=" +
          "org.apache.beam.sdk.extensions.gcp.auth.NoopCredentialFactory",
        // Pipeline-private args parsed by ``CustomersPipeline.run``
        // — Beam consumes ``--project`` ahead of these.
        s"--bqEmulatorEndpoint=$rest",
        "--bqProject=bqemu-demo",
        "--bqDataset=scio_demo",
      ))
      written shouldBe 3L

      // 4. Round-trip — query the emulator's BigQuery REST surface
      //    and assert the LOAD job actually landed 3 rows on the
      //    destination table.
      val countQ = HttpRequest.newBuilder()
        .uri(URI.create(s"$rest/bigquery/v2/projects/bqemu-demo/queries"))
        .header("Content-Type", "application/json")
        .timeout(timeout)
        .POST(HttpRequest.BodyPublishers.ofString(
          """{"query":"SELECT COUNT(*) AS n FROM `bqemu-demo`.scio_demo.customers","useLegacySql":false}""",
        ))
        .build()
      val countResp =
        client.send(countQ, HttpResponse.BodyHandlers.ofString())
      withClue(
        s"count query failed: ${countResp.statusCode()} ${countResp.body()}",
      ) {
        countResp.statusCode() shouldBe 200
      }
      // The wire shape is ``rows: [{f: [{v: "3"}]}]`` — BigQuery
      // serialises all scalar column values as JSON strings.
      countResp.body() should include(""""v":"3"""")
    } finally {
      bqemu.stop()
      fakeGcs.stop()
      net.close()
    }
  }
}
