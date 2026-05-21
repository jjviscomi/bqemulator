# PySpark reading from `bqemulator`

Reads data from `bqemulator` via the BigQuery Storage Read API (Arrow
output) into a PySpark DataFrame using the
[`google-cloud-bigquery-storage`](https://pypi.org/project/google-cloud-bigquery-storage/)
client and Spark's `createDataFrame` from an Arrow table.

Pairs with the [Spark integration guide](../../../guides/spark-integration.md).

## Why this shape (vs. the official `spark-bigquery-connector`)

The official
[`spark-bigquery-connector`](https://github.com/GoogleCloudDataproc/spark-bigquery-connector)
is a Scala/Java JAR that talks to the real BigQuery Storage Read API
over gRPC. Pointing it at an emulator host requires JVM-level
`--conf` overrides that vary by connector version and tend to break
on upgrades.

The robust, version-stable pattern is:

1. Use the
   [`google-cloud-bigquery-storage`](https://github.com/googleapis/python-bigquery-storage)
   Python client (which respects the standard endpoint override
   semantics) to read rows out of BigQuery as Arrow record batches.
2. Hand the Arrow table to Spark via `spark.createDataFrame`.

This matches the surface `bqemulator` exposes (see
[ADR 0030](../../../adr/0030-storage-read-avro-format.md)) and is the
pattern used in the [Storage Read API guide](../../../guides/storage-api.md).

## What it demonstrates

- Seeding a 5-row table via the standard `google-cloud-bigquery`
  client against the emulator.
- Reading those rows back via the Storage Read API
  (`google-cloud-bigquery-storage`) with `DataFormat.ARROW`.
- Constructing a Spark `DataFrame` from the result and running an
  aggregate.
- Asserting the round-tripped count matches.

## Layout

```
example.py — full demo: seed, Storage Read, Spark aggregate
```

## Run

```bash
make test
```

## What to look for

- The Storage Read client is constructed with
  `client_options=ClientOptions(api_endpoint=grpc_endpoint)` pointing
  at the emulator's gRPC port (not the REST port).
- Spark runs in local-master mode (`local[*]`) so the example is
  hermetic — no Hadoop/YARN cluster required.
