package com.example.bqemu

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName

class CustomersPipelineSpec extends AnyFlatSpec with Matchers {

  "CustomersPipeline" should "write 3 rows to bqemulator via the Direct runner" in {
    val image = sys.env.getOrElse("BQEMU_IMAGE", "ghcr.io/jjviscomi/bqemulator:dev")

    val container = new GenericContainer(DockerImageName.parse(image))
      .withEnv("BQEMU_REST_HOST", "0.0.0.0")
      .withEnv("BQEMU_GRPC_HOST", "0.0.0.0")
      .withEnv("BQEMU_ADMIN_ENABLED", "1")
      .withExposedPorts(9050, 9060)
      .waitingFor(Wait.forHttp("/healthz").forPort(9050))

    container.start()
    try {
      val rest = s"http://${container.getHost}:${container.getMappedPort(9050)}"
      val args = Array(
        "--runner=DirectRunner",
        s"--bigQueryEndpoint=$rest",
        "--project=bqemu-demo",
        "--dataset=scio_demo"
      )
      val written = CustomersPipeline.run(args)
      written shouldBe 3L
    } finally {
      container.stop()
    }
  }
}
