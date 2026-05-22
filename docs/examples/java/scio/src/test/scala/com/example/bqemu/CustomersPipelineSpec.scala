package com.example.bqemu

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

  "CustomersPipeline" should "write 3 rows to bqemulator via the Direct runner" in {
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
