# ADR 0013: Strategy-pattern Storage Write API with in-memory stream state

- **Status**: Accepted

## Context

The real BigQuery Storage Write API supports four stream types with
distinct commit semantics:

* **DEFAULT** — implicit per-table, always-open stream. Every AppendRows
  is immediately committed; no offset dedup.
* **COMMITTED** — immediate commit with strict offset-based exactly-once.
* **PENDING** — buffers rows; visible only after
  ``FinalizeWriteStream`` + ``BatchCommitWriteStreams``.
* **BUFFERED** — buffers rows; visible after ``FlushRows(offset)``.

Rows arrive in two formats: Arrow IPC record batches or inline
dynamic-protobuf messages whose ``DescriptorProto`` is sent in the first
``AppendRowsRequest``. A production implementation would back this with
MVCC + a WAL to survive process restarts.

## Decision

1. **Strategy pattern** for stream semantics.
   Each stream type maps to a ``WriteStrategy`` subclass
   (``DefaultWriteStrategy``, ``CommittedWriteStrategy``,
   ``PendingWriteStrategy``, ``BufferedWriteStrategy``) exposing
   ``append``, ``flush``, and ``commit``. ``select_strategy`` dispatches.
2. **In-memory stream state.**
   A ``WriteStreamManager`` holds every stream as a dataclass keyed by
   its fully-qualified name. State is lost on process restart — this
   matches the emulator's ephemeral-by-default persistence model. The
   manager uses a ``threading.RLock`` for concurrent reads/writes.
3. **Generic gRPC handler**, same pattern as Phase 4's Read API.
   No vendored proto stubs; the servicer deserialises requests with the
   proto-plus types from ``google-cloud-bigquery-storage``.
4. **Dynamic protobuf deserialisation.**
   Each AppendRows connection builds a ``ProtoRowDecoder`` from the
   first ``writer_schema`` using
   ``google.protobuf.descriptor_pool.DescriptorPool`` +
   ``message_factory.GetMessageClass``. Subsequent messages on the same
   connection can omit the schema.
5. **Threading lock around DuckDB writes.**
   Sync handlers run on grpc.aio's thread pool; the engine's
   ``write_lock`` is an ``asyncio.Lock`` and can't be awaited from sync
   code, so the servicer has its own ``threading.RLock`` that serialises
   ``INSERT INTO`` + ``update_table``. A per-call unique register name
   (``__bqemu_write_<hex>``) prevents name collisions between
   interleaved writers.
6. **Two-pass BatchCommitWriteStreams.**
   Pass 1 validates every referenced stream (existence, type, finalized
   state) and collects errors. Pass 2 only runs if pass 1 succeeded;
   it commits each stream's buffer and flushes to DuckDB. This makes
   BatchCommit atomic from the client's point of view — a partial
   failure never leaves committed-but-not-flushed state behind.
7. **Idempotent FinalizeWriteStream.**
   Repeated Finalize calls on an already-finalized stream succeed
   without double-decrementing the ``write_streams_active`` gauge.

## Rationale

- Dispatching commit semantics via strategy gives each stream type an
  isolated, framework-free unit that's straightforwardly unit-testable.
- An in-memory manager is sufficient because:
 1. The emulator is single-process.
 2. EPHEMERAL is the default persistence mode (tests, CI, ad-hoc dev).
    Durable stream state across restarts isn't a use case for the
    emulator; persistent mode already warns that long-running flows
    should drain before shutdown.
- The generic-handler approach reproduces the design decision validated
  in Phase 4 (ADR 0008 context) — zero build-time dependency on
  ``protoc``, full compatibility with official client libraries.
- A ``DescriptorPool`` per decoder isolates dynamic types so multiple
  connections in the same process don't collide on proto names.

## Consequences

- **Positive**: strategy files stay small (30–100 LOC each) and can be
  tested without DuckDB or gRPC.
- **Positive**: tests and clients cover both Arrow and proto paths in
  the same integration suite.
- **Positive**: no protoc/grpc_tools dependency at build time.
- **Negative**: stream state is not durable. Documented here and in
  ``docs/reference/out-of-scope.md`` (persistence upgrade is a v2
  candidate).
- **Negative**: very large PENDING/BUFFERED accumulations live in RAM.
  Acceptable for emulator workloads; the observability layer reports
  active streams per type so users can see accumulation.
