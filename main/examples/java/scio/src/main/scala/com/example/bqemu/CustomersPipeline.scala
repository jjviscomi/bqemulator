package com.example.bqemu

import com.google.api.services.bigquery.model.{TableFieldSchema, TableRow, TableSchema}
import com.spotify.scio.ContextAndArgs
import com.spotify.scio.bigquery._

import scala.jdk.CollectionConverters._

/** Writes a tiny customers table to BigQuery and reads it back.
  *
  * Designed to run against either real BigQuery (Dataflow) or bqemulator
  * (DirectRunner + --bigQueryEndpoint=http://localhost:PORT).
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
    val table   = s"$project:$dataset.customers"

    val schema = new TableSchema().setFields(List(
      new TableFieldSchema().setName("id").setType("INTEGER"),
      new TableFieldSchema().setName("name").setType("STRING")
    ).asJava)

    val rows = sc.parallelize(Seq(
      new TableRow().set("id", 1L).set("name", "Alice"),
      new TableRow().set("id", 2L).set("name", "Bob"),
      new TableRow().set("id", 3L).set("name", "Carol")
    ))

    rows.saveAsBigQueryTable(
      Table.Spec(table),
      schema = schema,
      writeDisposition = WRITE_TRUNCATE,
      createDisposition = CREATE_IF_NEEDED
    )

    sc.run().waitUntilFinish()
    3L
  }

  def main(args: Array[String]): Unit = {
    val written = run(args)
    println(s"Wrote $written rows.")
  }
}
