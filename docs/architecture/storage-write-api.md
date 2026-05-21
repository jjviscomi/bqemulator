# Storage Write API

Implementation in [write_servicer.py](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/grpc_api/write_servicer.py) +
[streaming/write_stream.py](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/streaming/write_stream.py) +
[streaming/strategies/](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/streaming/strategies).

## Design summary

| Decision | File | Rationale |
|---|---|---|
| Generic gRPC handler (no vendored protos) | `grpc_api/write_servicer.py` | Same pattern as the Storage Read API; zero `protoc` dependency at build time. |
| Strategy per stream type | `streaming/strategies/*.py` | Each stream type has its own commit rules; isolating them keeps each file ≤100 LOC and unit-testable without gRPC/DuckDB. |
| In-memory `WriteStreamManager` | `streaming/write_stream.py` | Emulator is single-process; state is intentionally lost on restart. Documented in [ADR 0013](../adr/0013-write-api-strategies.md). |
| Dynamic protobuf via `DescriptorPool` | `streaming/proto_deserializer.py` | Writer schema is inline — we build the message class at runtime. |

## Strategy per stream type

| Strategy | File | Semantics |
|---|---|---|
| `DefaultWriteStrategy` | `strategies/default.py` | Writes immediately; rejects any offset. |
| `CommittedWriteStrategy` | `strategies/committed.py` | Writes immediately; offsets strictly monotonic starting at 0. |
| `PendingWriteStrategy` | `strategies/pending.py` | Buffers in memory; flushes on `FinalizeWriteStream` + `BatchCommitWriteStreams`. |
| `BufferedWriteStrategy` | `strategies/buffered.py` | Buffers; flushes on `FlushRows(offset)`. |

The selector is `streaming.strategies.select_strategy(stream_type)`.

## AppendRows bidi flow

```
client ──► AppendRowsRequest                         ──► servicer
                                                          │
                                       ┌──────────────────┤ (first message only)
                                       │
                                       ▼
                       resolve stream via WriteStreamManager
                             (auto-create DEFAULT on demand)
                                       │
                                       ▼
              build ProtoRowDecoder or remember arrow_schema bytes
                                       │
client ──► AppendRowsRequest ... ──►   │
                                       ▼
                           decode rows → pyarrow.Table
                                       │
                                       ▼
                 strategy.append(stream, rows, offset)
                   │                                     │
                 ok                                    error
                   │                                     │
                   ▼                                     ▼
    flush_to_target (under                  AppendRowsResponse with
    threading.RLock)                        `error` field; stream stays open
                   │
                   ▼
              AppendRowsResponse
              (offset echoed back)
```

## Offset semantics

| Scenario | Result |
|---|---|
| First append on a fresh stream, offset omitted | Accepted; starts at 0. |
| Offset == stream.next_offset | Accepted; advances. |
| Offset < stream.next_offset | `ALREADY_EXISTS` (client retrying a committed page). |
| Offset > stream.next_offset | `OUT_OF_RANGE` (client skipped a page). |
| Any offset on DEFAULT | `INVALID_ARGUMENT` (DEFAULT is offset-free). |

## State machine

```
                             CreateWriteStream
                                    │
                                    ▼
                                   OPEN
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
   (COMMITTED/BUFFERED)      (PENDING)                     (DEFAULT)
   FinalizeWriteStream     FinalizeWriteStream         (never transitions)
          │                         │
          ▼                         ▼
      FINALIZED                 FINALIZED
                                    │
                                    │ BatchCommitWriteStreams
                                    ▼
                                COMMITTED
```

## Concurrency

- `WriteStreamManager` uses a `threading.RLock` for its dict mutations.
- The servicer has a second `threading.RLock` (`self._write_lock`) that
  serialises DuckDB writes. This is needed because sync gRPC handlers
  run on grpc.aio's thread pool and can't acquire the engine's
  ``asyncio.Lock``.
- Each ``_flush_to_target`` call uses a unique DuckDB register name
  (``__bqemu_write_<hex>``) so concurrent flushes on different streams
  never clash.

## Atomicity of BatchCommitWriteStreams

BatchCommit uses two passes:

1. **Validate**: for every referenced stream, check it exists, is
   PENDING, and is FINALIZED. Collect errors into `stream_errors`.
2. **Commit**: only if pass 1 had zero errors, call
   `strategy.commit(stream)` and flush each buffer to DuckDB.

This ensures the whole batch is atomic from the client's perspective:
a partial failure in pass 1 never leaves a stream committed-but-
not-flushed.

## Dynamic proto deserialization

Protobuf rows are decoded by a `ProtoRowDecoder` built from the
`ProtoSchema.proto_descriptor` in the first `AppendRowsRequest`:

1. Wrap the `DescriptorProto` in a `FileDescriptorProto` with a unique
   synthetic file name (`bqemu_dynamic_<uuid>.proto`).
2. `DescriptorPool.Add(file_proto)`.
3. `MessageFactory.GetMessageClass(descriptor)` → dynamic message class.
4. For every serialized row, `ParseFromString()` → `_message_to_dict()`
   → coerce values against the *target table* schema.

The decoder is cached on the servicer for the life of the AppendRows
bidi connection.

## Input formats

| Format | Wire | How rows are decoded |
|---|---|---|
| **Arrow** | `arrow_rows.writer_schema.serialized_schema` + `rows.serialized_record_batch` | Concatenate schema + batch bytes → `pyarrow.ipc.open_stream`. |
| **Protobuf** | `proto_rows.writer_schema.proto_descriptor` (`DescriptorProto`) + `rows.serialized_rows` (list[bytes]) | Dynamic `ProtoRowDecoder`. |

## Error surface

| Client action | Outcome |
|---|---|
| Invalid parent / table path | gRPC `INVALID_ARGUMENT`. |
| Table doesn't exist | gRPC `NOT_FOUND`. |
| Duplicate offset | `AppendRowsResponse.error.code = ALREADY_EXISTS` (stream stays open). |
| Offset gap | `AppendRowsResponse.error.code = OUT_OF_RANGE`. |
| Append to finalized stream | `AppendRowsResponse.error.code = FAILED_PRECONDITION`. |
| Flush on non-BUFFERED stream | gRPC `INVALID_ARGUMENT`. |
| Commit on non-PENDING stream | Listed in `stream_errors` with `INVALID_STREAM_TYPE`. |
| Commit of non-finalized PENDING stream | Listed in `stream_errors` with `INVALID_STREAM_STATE`. |

## Metrics

`bqemulator_write_streams_active{stream_type="..."}` is incremented on
CreateWriteStream and decremented on FinalizeWriteStream. DEFAULT
streams are not counted (they're per-table and always-open).
