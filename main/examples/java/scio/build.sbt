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
      "org.testcontainers" % "testcontainers" % "1.19.7" % Test
    ),
    dependencyOverrides ++= Seq(
      "com.fasterxml.jackson.core" % "jackson-core" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-databind" % jacksonVersion,
      "com.fasterxml.jackson.core" % "jackson-annotations" % jacksonVersion,
      "com.fasterxml.jackson.module" %% "jackson-module-scala" % jacksonVersion
    ),
    Test / parallelExecution := false,
    Test / fork := true
  )
