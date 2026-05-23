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
      // (27+) returns ``client version 1.32 is too old``. 1.21.x
      // bundles the newer docker-java that handles current daemons.
      "org.testcontainers" % "testcontainers" % "1.21.4" % Test
    ),
    dependencyOverrides ++= Seq(
      "com.fasterxml.jackson.core" % "jackson-core" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-databind" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-annotations" % jacksonVersion,
      "com.fasterxml.jackson.module" %% "jackson-module-scala" % jacksonVersion
    ),
    Test / parallelExecution := false,
    Test / fork := true,
    // Defense in depth against host-side gcloud credentials leaking
    // into the test JVM. Beam's ``--gcpCredentialFactoryClass=Noop...``
    // is sufficient on its own (verified against Beam 2.55.1's
    // ``GcpUserCredentialsFactory.create`` honouring the factory
    // class — ``NoopCredentialFactory.getCredential()`` returns inert
    // ``NoopCredentials``), but a fresh-empty ``CLOUDSDK_CONFIG`` and
    // a deliberately-missing ``GOOGLE_APPLICATION_CREDENTIALS`` keep
    // the no-op-auth contract honest on developer laptops that have
    // ``gcloud auth application-default login`` state laying around.
    Test / envVars := {
      val emptyConfig = java.nio.file.Files
        .createTempDirectory("bqemu-scio-empty-cloudsdk")
        .toString
      Map(
        "CLOUDSDK_CONFIG" -> emptyConfig,
        "GOOGLE_APPLICATION_CREDENTIALS" -> "/nonexistent/bqemu-scio-no-creds.json",
      )
    },
  )
