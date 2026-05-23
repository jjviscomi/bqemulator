# Apache Spark integration

Status: shipped (runnable example in `docs/examples/python/pyspark-bigquery/`).

The [spark-bigquery-connector](https://github.com/GoogleCloudDataproc/spark-bigquery-connector)
uses the Storage Read API for high-throughput reads. Point it at the
emulator:

```python
spark.conf.set("viewsEnabled", "true")
df = (
    spark.read
        .format("bigquery")
        .option("project", "test-project")
        .option("parentProject", "test-project")
        .option("endpoint", "localhost:9060")   # gRPC
        .option("useAvroLogicalTypes", "true")
        .load("sales.orders")
)
```

Storage Write API sink writes are equivalently configured. See the
example project for a complete Spark job.

## Storage Read API — bare record-batch IPC contract (v1.0.1)

Under v1.0.0 the server packed a full Arrow IPC stream
(schema-message + batches + EOS marker) into
``ReadRowsResponse.arrow_record_batch.serialized_record_batch``. Real
BigQuery sends only the record-batch bytes — the schema travels on
``ReadSession.arrow_schema.serialized_schema``. The mismatch tripped
the canonical ``google-cloud-bigquery-storage``
``reader.to_arrow(session)`` helper with
``Expected IPC message of type record batch but got schema``.

v1.0.1 ([#15](https://github.com/jjviscomi/bqemulator/issues/15) /
[ADR 0033](../adr/0033-storage-read-arrow-ipc-bare-message-contract.md))
shipped the spec-conforming bare record-batch format. The canonical
helper now works against bqemulator unchanged:

```python
arrow_table = reader.to_arrow(session)
```

If you're pinning to v1.0.0 you still need the
``pyarrow.ipc.open_stream`` workaround — upgrade to v1.0.1+ to use
the canonical path.

> **Note:** Dictionary-encoded columns (at any nesting depth in
> structs / lists / maps / unions) are rejected by the v1.0.1
> producer with a clear ``ValueError`` rather than silently produce
> a payload the consumer can't decode. See ADR 0033 for the formal
> contract.

## Plaintext gRPC

bqemulator serves the Storage Read API over plaintext gRPC. The
default `BigQueryReadClient` transport wraps every call in TLS,
which fails the handshake against a plaintext endpoint
(`SSL_ERROR_SSL: WRONG_VERSION_NUMBER`). Construct an
`grpc.insecure_channel` explicitly and pass it via the transport:

```python
import grpc
from google.cloud import bigquery_storage_v1
from google.cloud.bigquery_storage_v1.services.big_query_read.transports import (
    BigQueryReadGrpcTransport,
)

channel = grpc.insecure_channel("localhost:9060")
transport = BigQueryReadGrpcTransport(channel=channel)
storage = bigquery_storage_v1.BigQueryReadClient(transport=transport)
```
