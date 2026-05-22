ThisBuild / scalaVersion := "2.13.13"
ThisBuild / organization := "com.example.bqemu"

lazy val scioVersion = "0.14.4"
lazy val beamVersion = "2.55.1"
// Scio 0.14.4 ships the Jackson Scala module pinned to 2.14.x, but a
// transitive dep (commonly logback / arrow) drags in jackson-databind
// 2.16.x. The Scala module's runtime guard refuses to load against a
// mismatched databind and the test aborts with
// ``Scala module 2.14.1 requires Jackson Databind version
//   >= 2.14.0 and < 2.15.0 - Found jackson-databind version 2.16.0``.
// Hold the whole jackson stack at 2.14.3 so the runtime check passes.
lazy val jacksonVersion = "2.14.3"

lazy val root = (project in file("."))
  .settings(
    name := "bqemu-scio-example",
    libraryDependencies ++= Seq(
      "com.spotify" %% "scio-core" % scioVersion,
      "com.spotify" %% "scio-google-cloud-platform" % scioVersion,
      "org.apache.beam" % "beam-runners-direct-java" % beamVersion,
      "ch.qos.logback" % "logback-classic" % "1.4.14" % Runtime,
      "com.spotify" %% "scio-test" % scioVersion % Test,
      "org.scalatest" %% "scalatest" % "3.2.18" % Test,
      // ``testcontainers`` 1.19.x ships docker-java 1.32 which only
      // talks to docker daemon API < 1.40 — modern Docker Desktop
      // (27+) returns ``client version 1.32 is too old``. 1.20.x
      // bundles the newer docker-java that handles current daemons.
      "org.testcontainers" % "testcontainers" % "1.20.4" % Test
    ),
    dependencyOverrides ++= Seq(
      "com.fasterxml.jackson.core" % "jackson-core" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-databind" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-annotations" % jacksonVersion,
      "com.fasterxml.jackson.module" %% "jackson-module-scala" % jacksonVersion
    ),
    Test / parallelExecution := false,
    Test / fork := true,
    // Beam Java BigQueryIO's write path uses the official Java
    // ``google-cloud-bigquery`` client internally, which reads
    // ``BIGQUERY_EMULATOR_HOST`` from the *process* environment when
    // the client is built. ``System.getenv`` is immutable after JVM
    // start, so the env var has to be present on the forked test JVM
    // before it boots — which is exactly what ``Test / envVars``
    // configures. Pair this with the fixed host port the spec binds
    // for the container (``BqemuContainer`` in
    // ``CustomersPipelineSpec``), so the JVM-start env var and the
    // container's actual listener agree. Override per-developer via
    // ``BQEMU_TEST_HOST_PORT=NNNN sbt test`` if 9099 is taken.
    Test / envVars := Map(
      "BIGQUERY_EMULATOR_HOST" -> s"localhost:${sys.env.getOrElse("BQEMU_TEST_HOST_PORT", "9099")}"
    )
  )
