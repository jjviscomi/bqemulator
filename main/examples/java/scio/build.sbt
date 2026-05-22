ThisBuild / scalaVersion := "2.13.13"
ThisBuild / organization := "com.example.bqemu"

lazy val scioVersion = "0.14.4"
lazy val beamVersion = "2.55.1"

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
    Test / parallelExecution := false,
    Test / fork := true
  )
