# Storage API

Status: shipped — both Storage Read and Storage Write.

## Storage Read API

`CreateReadSession` materializes your projection + filter as a
`pyarrow.Table` at session creation (see
[ADR 0008](../adr/0008-snapshot-storage-read-api.md)). `ReadRows` streams
slices of that snapshot in the session's chosen wire format.

### Choosing Arrow vs Avro

| Format | Default in | When to pick |
|---|---|---|
| **Arrow IPC** | Python, Go, Node | The fastest read path — zero-copy into pyarrow / arrow-js / arrow-go memory. Pick this unless you have a specific reason to use Avro. |
| **Apache Avro** | **Java** | Java's `BigQueryReadClient.create().createReadSession(...)` defaults to Avro. Lower overhead in JVM consumers that already have an Avro pipeline, and matches BigQuery's documented Avro export shape. See [ADR 0030](../adr/0030-storage-read-avro-format.md). |

The proto3 default for an unset `data_format` is treated as Arrow,
matching real BigQuery. Any other value (a hypothetical future
`PROTO` format) surfaces `INVALID_ARGUMENT`.

The Avro wire shape is "schema-once on the session, naked binary rows
per response chunk" — `ReadSession.avro_schema.schema` carries the
writer schema as JSON; each `ReadRowsResponse.avro_rows.serialized_binary_rows`
carries Avro's binary encoding back-to-back with **no** Avro Object
Container File header. Decode with `fastavro.schemaless_reader` (Python),
`org.apache.avro.io.DatumReader<GenericRecord>` (Java),
`@google-cloud/bigquery-storage`'s built-in Avro decoder (Node), or
`github.com/linkedin/goavro` (Go).

## Storage Write API

Four stream types, all fully supported in v1 — see
[ADR 0013](../adr/0013-write-api-strategies.md) for the design.

| Stream type | Visibility | Commit required | Offset dedup |
|---|---|---|---|
| `DEFAULT` | Immediate (at-least-once) | No | No |
| `COMMITTED` | Immediate (exactly-once) | No | Yes |
| `PENDING` | On `BatchCommitWriteStreams` | Yes | Yes |
| `BUFFERED` | On `FlushRows` | Flush | Yes |

Both Arrow and dynamic-protobuf row formats are accepted.

### Quickstart (Python)

```python
from google.cloud import bigquery_storage_v1
from google.cloud.bigquery_storage_v1 import types, writer
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
import grpc

# Point the Write client at the emulator's gRPC endpoint.
channel = grpc.insecure_channel("localhost:9060")
write_client = bigquery_storage_v1.BigQueryWriteClient(
    transport=bigquery_storage_v1.services.big_query_write.transports
        .BigQueryWriteGrpcTransport(channel=channel),
)

# Create a COMMITTED stream.
stream = write_client.create_write_stream(
    parent="projects/my-project/datasets/sales/tables/orders",
    write_stream=types.WriteStream(type_=types.WriteStream.Type.COMMITTED),
)

# AppendRows is a bidi stream — use the high-level AppendRowsStream helper or
# drive it manually via `channel.stream_stream(...)`.
```

### Choosing a stream type

* **Development loops / ad-hoc scripts** — use `DEFAULT`; no setup, no
  teardown. Rows are visible the moment AppendRows acknowledges.
* **Real production parity / exactly-once ingestion** — use `COMMITTED`.
  Keep a per-producer offset counter; retry with the same offset after a
  transient error.
* **Batch loads with an atomic swap** — use `PENDING`. Write N streams
  in parallel, then `FinalizeWriteStream` each, then commit them all in
  a single `BatchCommitWriteStreams` call. Either everything lands or
  nothing does.
* **Incremental flush** — use `BUFFERED`. Stage rows, then
  `FlushRows(offset)` when you want them visible. Useful when upstream
  data arrives in micro-batches and you want to hold back rows until
  they're certified clean.

### Error surface

| Client action | Signal |
|---|---|
| Duplicate offset on COMMITTED/PENDING/BUFFERED | `AppendRowsResponse.error.code = ALREADY_EXISTS` |
| Gap offset (offset > next_offset) | `AppendRowsResponse.error.code = OUT_OF_RANGE` |
| Append after Finalize | `AppendRowsResponse.error.code = FAILED_PRECONDITION` |
| Invalid stream name / missing table | gRPC `INVALID_ARGUMENT` / `NOT_FOUND` |
| Commit non-finalized PENDING stream | `stream_errors[...].code = INVALID_STREAM_STATE` |

### Limitations

- Stream state is in-memory; a process restart forgets every stream.
  This is intentional — see [ADR 0013](../adr/0013-write-api-strategies.md).
- Schema evolution (AppendRows updating `updated_schema`) is not yet
  emulated; the emulator always reports the target table schema.
