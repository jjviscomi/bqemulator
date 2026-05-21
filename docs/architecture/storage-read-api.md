# Storage Read API

Implementation in `src/bqemulator/grpc_api/read_servicer.py` +
`src/bqemulator/streaming/read_session.py` +
`src/bqemulator/streaming/avro_serializer.py`.

## Session lifecycle

1. Client calls `CreateReadSession(table, selected_fields, row_filter,
   max_streams, data_format)`.
2. Servicer dispatches on `data_format`:
 * `ARROW` (Python / Go / Node default; also the proto3 default for
   an unset field) — session emits Arrow IPC.
 * `AVRO` (Java client default) — session emits naked Avro binary
   rows (G3 / ADR 0030).
 * Any other value → `INVALID_ARGUMENT`.
3. Servicer builds a projection+filter query and executes it against
   DuckDB, materializing a `pyarrow.Table`.
4. Servicer splits the table into `N = min(max_streams, 10)` row ranges
   (with a small-table cap of 1 stream below ~1 MB) and records them
   as `ReadStream` references on a `ReadSessionState`. The session
   state carries the chosen `data_format` and (for AVRO sessions) a
   pre-computed Avro schema JSON so subsequent `ReadRows` calls and
   `SplitReadStream` children all emit the same format without
   re-deriving it.
5. Each stream has a name of the form
   `projects/{p}/locations/{loc}/sessions/{sid}/streams/{n}`.

## Read path

Client calls `ReadRows(stream_name)` (server-streaming RPC). The servicer
looks up the stream state, slices the materialized Arrow table for the
stream's row range, and streams chunks in the session's chosen format:

- **Arrow IPC**: via `pyarrow.ipc.RecordBatchStreamWriter` into a
  `BytesIO` buffer; each chunk carries an `ArrowRecordBatch` and the
  first chunk additionally carries the `arrow_schema`.
- **Avro**: via `fastavro.schemaless_writer` (G3 / ADR 0030); each
  chunk carries an `AvroRows.serialized_binary_rows` payload and the
  first chunk additionally carries the `avro_schema`. The bytes are
  **naked** — NO Object Container File header per chunk — per
  BigQuery's documented wire contract.

## Consistency

Per [ADR 0008](../adr/0008-snapshot-storage-read-api.md), the
materialization IS the snapshot. Writes after session creation do not
affect in-flight sessions.

## SplitReadStream

The API supports subdividing a stream further. The servicer re-splits the
stream's row range into two halves and returns the new stream names.
Both child streams inherit the parent session's `data_format` (a child
of an Avro session continues serving Avro).
