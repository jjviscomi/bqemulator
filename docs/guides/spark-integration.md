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

## Known limitation — Storage Read IPC format

bqemulator currently packs the full Arrow IPC stream (schema framing
+ batches) into `ReadRowsResponse.arrow_record_batch.serialized_record_batch`
instead of a single record-batch IPC message. Real BigQuery sends just
the record-batch bytes — the schema travels on
`ReadSession.arrow_schema.serialized_schema`. The
`google-cloud-bigquery-storage` client's high-level
`reader.to_arrow(session)` helper assumes the real-BigQuery shape and
trips `Expected IPC message of type record batch but got schema`.

Workaround (used by the pyspark-bigquery example): iterate the
responses by hand and use `pyarrow.ipc.open_stream`, which accepts
the full IPC stream that bqemulator emits.

```python
import pyarrow as pa

batches: list[pa.RecordBatch] = []
for response in reader:
    payload = response.arrow_record_batch.serialized_record_batch
    if not payload:
        continue
    with pa.ipc.open_stream(payload) as stream_reader:
        batches.extend(stream_reader.read_all().to_batches())
arrow_table = pa.Table.from_batches(batches)
```

Tracked for cleanup in
[#15](https://github.com/jjviscomi/bqemulator/issues/15) — once the
server emits the correct format the workaround disappears and
`reader.to_arrow(session)` works out of the box.

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
