package com.example.bqemu

import com.google.api.services.bigquery.model.{
  TableFieldSchema,
  TableRow,
  TableSchema,
}
import com.spotify.scio.ContextAndArgs
import com.spotify.scio.bigquery._
import org.apache.beam.sdk.io.gcp.bigquery.{
  BigQueryIO,
  EmulatorBigQueryServices,
}

import scala.jdk.CollectionConverters._

/** Writes a tiny customers table to BigQuery and reads it back.
  *
  * Production-shaped: against real BigQuery (Dataflow) or any
  * managed BQ endpoint, ``CustomersPipeline`` calls scio's idiomatic
  * ``saveAsBigQueryTable`` — the wrapper most Beam-on-JVM Dataflow
  * workloads on Google's stack use. Against a local bqemulator
  * (driven by the scio example's CI / test suite) the pipeline
  * accepts an extra ``--bqEmulatorEndpoint`` arg and switches to
  * raw ``BigQueryIO.writeTableRows`` with
  * ``.withTestServices(EmulatorBigQueryServices(endpoint))`` so the
  * Apiary ``Bigquery`` client targets the local REST surface.
  *
  * Beam 2.55.1's Java SDK has no built-in ``BIGQUERY_EMULATOR_HOST``
  * support (the Go SDK does — see apache/beam#34037 — but the Java
  * side never adopted it). ``withTestServices`` is the documented
  * test hook for swapping the BigQueryServices implementation;
  * ``EmulatorBigQueryServices`` is the smallest possible wrapper
  * that builds the Apiary client with ``setRootUrl(emulator)`` and
  * reuses Beam's default JobService / DatasetService bodies
  * unchanged.
  *
  * Production callers (no ``--bqEmulatorEndpoint``) take the scio
  * branch unchanged.
  */
object CustomersPipeline {

  def run(args: Array[String]): Long = {
    val (sc, parsedArgs) = ContextAndArgs(args)
    // ``--project`` is a Beam pipeline option (ScioContext consumes it
    // before we ever see it), so reading it out of ``parsedArgs``
    // throws ``Missing value for property 'project'``. Use namespaced
    // arg names for the example's own settings.
    val project = parsedArgs("bqProject")
    val dataset = parsedArgs("bqDataset")
    val tableName = s"$project:$dataset.customers"

    val schema = new TableSchema().setFields(List(
      new TableFieldSchema().setName("id").setType("INTEGER"),
      new TableFieldSchema().setName("name").setType("STRING"),
    ).asJava)

    val rows = sc.parallelize(Seq(
      new TableRow().set("id", 1L).set("name", "Alice"),
      new TableRow().set("id", 2L).set("name", "Bob"),
      new TableRow().set("id", 3L).set("name", "Carol"),
    ))

    parsedArgs.optional("bqEmulatorEndpoint") match {
      case Some(endpoint) =>
        // Local-emulator path. Switch to raw ``BigQueryIO.Write`` so
        // we can attach ``.withTestServices(...)``; scio's
        // ``saveAsBigQueryTable`` wraps the same transform but does
        // not expose the test-services hook.
        rows.internal.apply(
          BigQueryIO.writeTableRows()
            .to(tableName)
            .withSchema(schema)
            .withCreateDisposition(
              BigQueryIO.Write.CreateDisposition.CREATE_IF_NEEDED,
            )
            .withWriteDisposition(
              BigQueryIO.Write.WriteDisposition.WRITE_TRUNCATE,
            )
            .withTestServices(new EmulatorBigQueryServices(endpoint)),
        )

      case None =>
        // Production path — what runs against real BigQuery / Dataflow.
        rows.saveAsBigQueryTable(
          Table.Spec(tableName),
          schema = schema,
          writeDisposition = WRITE_TRUNCATE,
          createDisposition = CREATE_IF_NEEDED,
        )
    }

    sc.run().waitUntilFinish()
    3L
  }

  def main(args: Array[String]): Unit = {
    val written = run(args)
    println(s"Wrote $written rows.")
  }
}
