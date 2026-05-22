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

- The Storage Read client is constructed with a `BigQueryReadGrpcTransport`
  wrapping an explicit `grpc.insecure_channel(...)` — bqemulator
  serves the Storage Read API over plaintext gRPC, and the default
  transport wraps every call in TLS (failing the handshake with
  `SSL_ERROR_SSL: WRONG_VERSION_NUMBER`).
- Spark runs in local-master mode (`local[*]`) so the example is
  hermetic — no Hadoop/YARN cluster required.

## Known limitation — Storage Read IPC bytes layout

bqemulator currently packs the full Arrow IPC stream (schema framing
+ batches) into `ReadRowsResponse.arrow_record_batch.serialized_record_batch`
instead of a single record-batch IPC message. The high-level
`reader.to_arrow(session)` helper assumes the real-BigQuery shape
(schema lives on `ReadSession.arrow_schema.serialized_schema`,
batches travel on their own) and trips
`Expected IPC message of type record batch but got schema`.

The example works around this by iterating responses by hand and
using `pyarrow.ipc.open_stream`, which accepts the full IPC stream
that bqemulator emits. Tracked for cleanup in
[#15](https://github.com/jjviscomi/bqemulator/issues/15) — once the
server emits the correct format the workaround disappears and the
natural `reader.to_arrow(session)` call works out of the box.
