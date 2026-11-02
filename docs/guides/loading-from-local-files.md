# Loading from local files (upload host)

The emulator ships multipart and resumable upload host endpoints.
See [ADR 0029](../adr/0029-upload-host-endpoints.md).

This guide covers the standard local-file load idioms across the four
official client libraries and the `bq` CLI. The emulator's
`/upload/bigquery/v2/...` routes implement the same multipart /
resumable upload protocols that real BigQuery uses, so client code
runs unchanged.

## Quick reference

| Client | API | Protocol | Media part `Content-Type` |
|---|---|---|---|
| Python | `Client.load_table_from_file(file, …, size=n)`, `load_table_from_json`, `load_table_from_dataframe` | Multipart when the size is known and under 5 MiB, resumable otherwise | `*/*` |
| Python | `Client.load_table_from_file(file, …)` without `size` | Resumable | — |
| Node | `Table.createWriteStream(…)`, `Table.load(localPath, …)` | Multipart, at any size | `application/octet-stream` |
| Go | `Table.LoaderFrom(bigquery.NewReaderSource(r)).Run(ctx)` | Multipart below the upload chunk size (16 MiB default), resumable otherwise | Sniffed from the first 512 bytes |
| Java | `BigQuery.writer(WriteChannelConfiguration)` | Resumable | — |
| `bq` CLI | `bq load <local file>` | Resumable; multipart with `--noenable_resumable_uploads` or for an empty file | `application/octet-stream` |

All of them route through `/upload/bigquery/v2/projects/{p}/jobs`
rather than the data-plane `/bigquery/v2/projects/{p}/jobs` endpoint.

## Python

```python
import io
from google.cloud import bigquery

client = bigquery.Client(project="my-project")
job_config = bigquery.LoadJobConfig(
    source_format=bigquery.SourceFormat.CSV,
    skip_leading_rows=1,
    schema=[
        bigquery.SchemaField("id", "INTEGER"),
        bigquery.SchemaField("name", "STRING"),
    ],
    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
)
csv_bytes = b"id,name\n1,alice\n2,bob\n3,carol\n"
job = client.load_table_from_file(
    io.BytesIO(csv_bytes),
    "my-project.sales.customers",
    job_config=job_config,
)
job.result()  # waits for the load to complete
```

The Python client sends a single `multipart` request when it knows the
payload size and it is under 5 MiB: pass `size=` to
`load_table_from_file`, while `load_table_from_json` and
`load_table_from_dataframe` always know it. Otherwise, including any file
object loaded without `size`, it uses `resumable`. The emulator handles
both shapes identically — the same `LoadJobConfig` flags apply.

## Node.js

```javascript
const { BigQuery } = require("@google-cloud/bigquery");

const bq = new BigQuery({ projectId: "my-project" });
await bq.dataset("sales").table("customers").load("./customers.csv", {
  sourceFormat: "CSV",
  skipLeadingRows: 1,
  writeDisposition: "WRITE_TRUNCATE",
  schema: { fields: [
    { name: "id", type: "INTEGER" },
    { name: "name", type: "STRING" },
  ] },
});
```

Given a local file path, `Table.load` sends the file as one multipart
request. To upload in-memory data, pipe it into
`Table.createWriteStream(metadata)`, which also sends one multipart
request. Given a Cloud Storage `File` instead, `Table.load` does not
upload anything: it submits a load job whose `sourceUris` names the
object's `gs://` URI, through the regular
`/bigquery/v2/projects/{p}/jobs` endpoint rather than the upload host.

## Go

```go
import (
    "bytes"
    "cloud.google.com/go/bigquery"
)

rs := bigquery.NewReaderSource(bytes.NewReader(csvBytes))
rs.SourceFormat = bigquery.CSV
rs.SkipLeadingRows = 1
rs.Schema = bigquery.Schema{
    {Name: "id", Type: bigquery.IntegerFieldType},
    {Name: "name", Type: bigquery.StringFieldType},
}
loader := client.Dataset("sales").Table("customers").LoaderFrom(rs)
loader.WriteDisposition = bigquery.WriteTruncate
job, err := loader.Run(ctx)
status, err := job.Wait(ctx)
```

The Go client labels the media part with whatever
`http.DetectContentType` reports for the first 512 bytes — `text/plain`
for typical CSV and NDJSON, but `image/bmp` for a CSV whose header
starts with `BM` or `text/html` for one that starts with an HTML-like
tag. The label does not affect the load (see
[Supported formats](#supported-formats)).

## Java

```java
WriteChannelConfiguration cfg = WriteChannelConfiguration
    .newBuilder(TableId.of("my-project", "sales", "customers"))
    .setFormatOptions(FormatOptions.csv())
    .setSchema(schema)
    .setSkipLeadingRows(1L)
    .setWriteDisposition(JobInfo.WriteDisposition.WRITE_TRUNCATE)
    .build();
try (TableDataWriteChannel channel = client.writer(cfg)) {
    byte[] csv = "id,name\n1,alice\n2,bob\n".getBytes(StandardCharsets.UTF_8);
    channel.write(ByteBuffer.wrap(csv));
}
```

## Supported formats

| `sourceFormat` | Notes |
|---|---|
| `CSV` | `autodetect` honored; CSV loads currently assume a header row (other CSV parsing knobs like `skipLeadingRows`, `fieldDelimiter`, and `quote` are not applied). |
| `NEWLINE_DELIMITED_JSON` | `autodetect` flag honored. |
| `PARQUET` | Schema inferred from file. |
| `AVRO` | Requires DuckDB's `avro` extension (G1, ADR 0027). |
| `ORC` | Requires `pip install bqemulator[orc]` (G1, ADR 0027). |

The bytes are always parsed per `sourceFormat`; the multipart media
part's `Content-Type` never selects the parser. As on BigQuery, whose
`jobs.insert` media upload accepts `*/*`, any media type is accepted —
`*/*`, `application/octet-stream`, a sniffed type such as `image/bmp`, or
no header at all — and the bytes are staged as sent (decoded only if the
part declares a `Content-Transfer-Encoding` such as `base64`). The one
exception is a composite type (`multipart/*` or `message/*`), which is
rejected with `400 invalidQuery`; see
[ADR 0049](../adr/0049-upload-media-part-content-type.md).

## Operator configuration

| Setting | Default | Reason |
|---|---|---|
| `BQEMU_UPLOAD_MAX_BYTES` | 1 GiB | Total bytes per upload. Cap is hard — uploads larger than this are rejected with HTTP 400 (`invalidQuery`) before disk write. |
| `BQEMU_UPLOAD_SESSION_TTL_SECONDS` | 3600 | How long an idle resumable session is retained before eviction. |
| `BQEMU_UPLOAD_STAGING_DIR` | (system tempdir) | Where staging temp files live. Set this to a known disk in CI to keep tempdir hygiene predictable. |

## Resumable protocol details

The resumable protocol is exposed as two phases that the client
libraries already implement:

1. **Initiate** — `POST /upload/bigquery/v2/projects/{p}/jobs?uploadType=resumable`
   with the `Job` resource as the JSON body. Response: `200 OK` with
   `Location: …?upload_id={session}` and
   `X-GUploader-UploadID: {session}` headers; empty body.
2. **Chunk upload** — `PUT /upload/bigquery/v2/projects/{p}/jobs?upload_id={session}`
   with the file bytes as the body and `Content-Range: bytes {start}-{end}/{total}`
   declaring the chunk's byte range. Each non-final chunk returns
   `308 Resume Incomplete` with `Range: bytes=0-{last_received}`.
   The final chunk returns `200 OK` with the `Job` resource.

A client that loses track of the offset can probe the session with
`PUT … Content-Range: bytes */{total}` (no body); the server replies
`308` with the `Range` header reflecting the current offset.

## Known limitations

- **Session state is in-memory.** A pod restart drops every in-progress
  upload; clients must restart from offset 0. See
  [out-of-scope.md#durable-upload-session-state](../reference/out-of-scope.md#durable-upload-session-state).
- **`uploadType=media` is rejected.** BigQuery itself rejects `media`
  for `jobs.insert`; the emulator mirrors the rejection. Use
  `multipart` or `resumable` instead.
- **Multipart envelope is parsed via the stdlib `email` package.**
  The boundary syntax follows RFC 2387 (`multipart/related`). Other
  multipart variants (`multipart/form-data`, etc.) are rejected.
- **Go client resumable uploads are rejected.** The Go client sends
  each resumable chunk as a `POST` to the session URI with
  `X-GUploader-No-308: yes`; the upload host accepts chunks only as
  `PUT` and treats that `POST` as a new session initiation, so a Go load
  larger than the client's upload chunk size (16 MiB by default) fails.
  Smaller Go loads go out as one multipart request and are unaffected.
- **Composite media-part types are rejected.** A media part labelled
  `multipart/*` or `message/*` returns `400 invalidQuery`; no official
  client sends one. See
  [out-of-scope.md](../reference/out-of-scope.md#composite-media-part-content-types-on-multipart-uploads).

## Runnable example

A complete runnable example lives at
[`docs/examples/local-file-load`](../examples/local-file-load/README.md) — a
single-file Python script that starts the emulator, runs multipart
uploads through `load_table_from_file` and `load_table_from_json`,
queries the rows back, and asserts. The Examples workflow runs it on
every PR to prevent doc rot.

## Schema Autodetection

Schema autodetection (using DuckDB's `read_csv_auto` or `read_json_auto`) only occurs when **both** of the following conditions are met:
1. The destination table does not already exist
2. No explicit `schema.fields` are provided in the load configuration

When these conditions are met and the `autodetect` flag is enabled for CSV or JSON loads, the emulator infers the schema by sampling the source data using DuckDB's native auto-detection capabilities.

**Note on CSV parsing:** DuckDB's `read_csv_auto` automatically sniffs the delimiter, header existence, and quote character independently of the explicit `fieldDelimiter`, `skipLeadingRows`, or `quote` properties specified in the load job configuration. For CSVs that lack headers or use exotic delimiters, schema inference may diverge from explicit load behavior; providing an explicit schema is recommended in these cases.

**Note on multi-file loads:** Schema inference is performed by sampling the *first* file in the `sourceUris` list. For multi-file loads where the schema drifts between files, the `COPY` operation may fail or write data incorrectly if subsequent files do not match the schema inferred from the first file.

**Note on nested types:** Nested data is inferred with full parity. A JSON object becomes a `RECORD`, a JSON array becomes a `REPEATED` field, and an array of objects becomes a `REPEATED RECORD`, recursively, matching what BigQuery's own autodetect produces (verified against recorded BigQuery responses). An array of arrays, which BigQuery's schema model cannot represent, is rejected with a clear error; provide an explicit `schema` if your data requires that shape.
