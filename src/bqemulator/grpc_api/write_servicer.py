"""BigQueryWrite gRPC service implementation.

Implements the Storage Write API v1 using the same generic-handler
pattern as the Read API: we don't vendor proto stubs, we
deserialize requests with the proto-plus types shipped by
``google-cloud-bigquery-storage``.

RPCs:
    CreateWriteStream — register a new stream for a target table.
    AppendRows — bidirectional streaming; append proto or Arrow rows.
    GetWriteStream — return metadata for an existing stream.
    FinalizeWriteStream — seal a stream against further appends.
    BatchCommitWriteStreams — atomically commit one or more PENDING streams.
    FlushRows — publish buffered rows up to an offset (BUFFERED only).

Design notes:

* ``_handle_append_rows`` is a sync generator. The generic-handler
  adapter funnels each inbound message into the generator and yields
  every outbound response back to the wire.
* Errors inside the AppendRows stream return an ``AppendRowsResponse``
  with an ``error`` status filled in — we *don't* close the stream on
  logical errors (that's how the real service behaves).
* Transport-level errors (unknown stream, bad schema) close the stream
  with a gRPC status code.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
import re
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import grpc

from bqemulator.domain.errors import ValidationError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.sql_identifiers import quoted_table_ref, register_name
from bqemulator.streaming.arrow_deserializer import deserialize_arrow_rows
from bqemulator.streaming.proto_deserializer import (
    ProtoRowDecoder,
    proto_rows_to_arrow_table,
)
from bqemulator.streaming.strategies import AppendOutcome, select_strategy
from bqemulator.streaming.strategies.base import AppendStatus
from bqemulator.streaming.write_stream import (
    DEFAULT_STREAM_SUFFIX,
    WriteStream,
    WriteStreamManager,
    WriteStreamState,
    WriteStreamType,
    parse_stream_name,
    parse_table_parent,
)

if TYPE_CHECKING:
    import pyarrow as pa

    from bqemulator.api.dependencies import AppContext
    from bqemulator.catalog.models import TableMeta

_log = get_logger(__name__)

_SERVICE = "/google.cloud.bigquery.storage.v1.BigQueryWrite"
_CREATE_WRITE_STREAM = f"{_SERVICE}/CreateWriteStream"
_APPEND_ROWS = f"{_SERVICE}/AppendRows"
_GET_WRITE_STREAM = f"{_SERVICE}/GetWriteStream"
_FINALIZE_WRITE_STREAM = f"{_SERVICE}/FinalizeWriteStream"
_BATCH_COMMIT_WRITE_STREAMS = f"{_SERVICE}/BatchCommitWriteStreams"
_FLUSH_ROWS = f"{_SERVICE}/FlushRows"

# Real BigQuery exposes the per-stream location in its WriteStream
# wire shape. The emulator is location-agnostic, so we report ``us``
# (lowercase, matching BQ's REST shape) — the gRPC-corpus
# conformance suite (P3.d) asserts the field's presence.
_STREAM_LOCATION = "us"

# Canonical stream-id regex: 16 hex chars OR the literal ``_default``
# sentinel. Used by ``GetWriteStream`` to reject malformed ids with
# INVALID_ARGUMENT — matching real BQ's wire shape rather than the
# emulator's earlier NOT_FOUND.
_STREAM_ID_RE = re.compile(r"^(?:[a-f0-9]{16}|_default)$")


def _table_arrow_schema(table_meta: TableMeta) -> pa.Schema:
    """Build a pyarrow schema for the target table from catalog metadata."""
    from bqemulator.api.routes.tabledata import _build_arrow_schema

    fields_raw = [
        {"name": f.name, "type": f.type, "mode": f.mode} for f in table_meta.schema_.fields
    ]
    return _build_arrow_schema(fields_raw)


def _proto_to_grpc_status(status: AppendStatus) -> grpc.StatusCode:
    mapping = {
        AppendStatus.ALREADY_EXISTS: grpc.StatusCode.ALREADY_EXISTS,
        AppendStatus.OUT_OF_RANGE: grpc.StatusCode.OUT_OF_RANGE,
        AppendStatus.STREAM_FINALIZED: grpc.StatusCode.FAILED_PRECONDITION,
        AppendStatus.INVALID_ARGUMENT: grpc.StatusCode.INVALID_ARGUMENT,
        AppendStatus.RESOURCE_EXHAUSTED: grpc.StatusCode.RESOURCE_EXHAUSTED,
    }
    return mapping.get(status, grpc.StatusCode.UNKNOWN)


class BigQueryWriteHandler(grpc.GenericRpcHandler):
    """Generic gRPC handler for the BigQuery Storage Write API."""

    def __init__(self, context: AppContext, manager: WriteStreamManager | None = None) -> None:
        self._ctx = context
        # Prefer the shared manager attached to the AppContext (built in
        # ``server.py``) so the admin /admin/streams endpoint sees the
        # same instance. Fall back to the optional injected manager (kept
        # for unit tests that build a handler outside the composition
        # root) or to a freshly-constructed one.
        self._manager = manager or context.write_streams
        # Install the metric-cleanup callback now that the context exists.
        self._manager.set_on_remove(self._release_metric_slot)
        # Sync handlers execute on grpc.aio's thread pool; a threading lock
        # serialises DuckDB writes because the event-loop-based
        # ``engine.write_lock`` can't be awaited from sync handlers.
        self._write_lock = RLock()

    def _release_metric_slot(self, stream: WriteStream) -> None:
        """Decrement ``write_streams_active`` when a stream is removed.

        Covers the "client forgot to Finalize" path so the gauge can't
        grow unboundedly over a long-running process.
        """
        if stream.metric_registered:
            self._ctx.metrics.write_streams_active.labels(
                stream_type=stream.stream_type.value,
            ).dec()
            stream.metric_registered = False

    @property
    def manager(self) -> WriteStreamManager:
        """Expose the manager for tests and servicer introspection."""
        return self._manager

    def service(
        self,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Route inbound RPCs to their specific handler."""
        method = handler_call_details.method
        if method == _CREATE_WRITE_STREAM:
            return grpc.unary_unary_rpc_method_handler(self._handle_create_write_stream)
        if method == _APPEND_ROWS:
            return grpc.stream_stream_rpc_method_handler(self._handle_append_rows)
        if method == _GET_WRITE_STREAM:
            return grpc.unary_unary_rpc_method_handler(self._handle_get_write_stream)
        if method == _FINALIZE_WRITE_STREAM:
            return grpc.unary_unary_rpc_method_handler(self._handle_finalize_write_stream)
        if method == _BATCH_COMMIT_WRITE_STREAMS:
            return grpc.unary_unary_rpc_method_handler(
                self._handle_batch_commit_write_streams,
            )
        if method == _FLUSH_ROWS:
            return grpc.unary_unary_rpc_method_handler(self._handle_flush_rows)
        return None

    # -- CreateWriteStream ---------------------------------------------------

    def _handle_create_write_stream(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Create a new write stream for a table."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.CreateWriteStreamRequest.deserialize(request_bytes)

        try:
            project_id, dataset_id, table_id = parse_table_parent(request.parent)
        except ValueError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return b""

        # Validate against BigQuery id rules so malicious names can't reach
        # the SQL layer via the stream's in-memory state.
        try:
            quoted_table_ref(project_id, dataset_id, table_id)
        except ValidationError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return b""

        table_meta = self._ctx.catalog.get_table(project_id, dataset_id, table_id)
        if table_meta is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(
                f"Table {project_id}.{dataset_id}.{table_id} not found",
            )
            return b""

        requested_type = request.write_stream.type_
        stream_type = _proto_type_to_strategy(requested_type)
        if stream_type is None:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(
                f"Unsupported stream type: {types.WriteStream.Type(requested_type).name}",
            )
            return b""

        stream_id = uuid4().hex[:16]
        stream = self._manager.create(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            stream_id=stream_id,
            stream_type=stream_type,
        )

        self._ctx.metrics.write_streams_active.labels(
            stream_type=stream_type.value,
        ).inc()
        # Remember that this stream's Create-side increment needs to be
        # balanced on Finalize OR on manager cleanup — see
        # ``WriteStreamManager.delete``.
        stream.metric_registered = True

        # Real BigQuery's CreateWriteStream response omits the
        # ``location`` field (empty default), whereas GetWriteStream
        # includes it. The wire-format conformance suite (P3.d) asserts
        # the asymmetry.
        response = types.WriteStream(
            name=stream.name,
            type_=requested_type,
            create_time=_now_timestamp(),
            table_schema=_table_meta_to_proto_schema(table_meta),
        )
        return types.WriteStream.serialize(response)

    # -- GetWriteStream ------------------------------------------------------

    def _handle_get_write_stream(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Return metadata for an existing write stream."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.GetWriteStreamRequest.deserialize(request_bytes)

        # Validate the stream name shape up-front so a malformed id
        # surfaces as INVALID_ARGUMENT (matching real BigQuery) rather
        # than NOT_FOUND. Stream ids are 16-hex-char unique tokens or
        # the literal ``_default`` sentinel; anything else is a client
        # bug and gets the canonical wire-shape error envelope.
        if not _is_valid_stream_name(request.name):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Invalid stream name. Entity: {request.name}")
            return b""

        stream = self._manager.get(request.name)

        # Auto-create the implicit DEFAULT stream if the client fetched it.
        if stream is None and request.name.endswith(f"/streams/{DEFAULT_STREAM_SUFFIX}"):
            try:
                project_id, dataset_id, table_id, _ = parse_stream_name(request.name)
            except ValueError as exc:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(str(exc))
                return b""
            if self._ctx.catalog.get_table(project_id, dataset_id, table_id) is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Requested entity was not found. Entity: {request.name}")
                return b""
            stream = self._manager.get_or_create_default(
                project_id,
                dataset_id,
                table_id,
            )

        if stream is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Requested entity was not found. Entity: {request.name}")
            return b""

        table_meta = self._ctx.catalog.get_table(
            stream.project_id,
            stream.dataset_id,
            stream.table_id,
        )
        response = types.WriteStream(
            name=stream.name,
            type_=_strategy_type_to_proto(stream.stream_type),
            create_time=_now_timestamp(),
            table_schema=_table_meta_to_proto_schema(table_meta) if table_meta else None,
            location=_STREAM_LOCATION,
        )
        return types.WriteStream.serialize(response)

    # -- FinalizeWriteStream -------------------------------------------------

    def _handle_finalize_write_stream(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Mark a stream as finalized; reject further appends."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.FinalizeWriteStreamRequest.deserialize(request_bytes)
        stream = self._manager.get(request.name)
        if stream is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Requested entity was not found. Entity: {request.name}")
            return b""

        if stream.stream_type is WriteStreamType.DEFAULT:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("DEFAULT streams cannot be finalized")
            return b""

        if stream.state is WriteStreamState.COMMITTED:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Stream has already been committed")
            return b""

        if stream.state is WriteStreamState.FINALIZED:
            # Idempotent: return the same row_count without double-decrementing
            # the active-stream gauge.
            response = types.FinalizeWriteStreamResponse(row_count=stream.row_count)
            return types.FinalizeWriteStreamResponse.serialize(response)

        stream.state = WriteStreamState.FINALIZED
        if stream.metric_registered:
            self._ctx.metrics.write_streams_active.labels(
                stream_type=stream.stream_type.value,
            ).dec()
            stream.metric_registered = False
        response = types.FinalizeWriteStreamResponse(row_count=stream.row_count)
        return types.FinalizeWriteStreamResponse.serialize(response)

    # -- BatchCommitWriteStreams --------------------------------------------

    def _handle_batch_commit_write_streams(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,  # noqa: ARG002 — errors returned in response
    ) -> bytes:
        """Atomically commit a set of PENDING streams."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.BatchCommitWriteStreamsRequest.deserialize(request_bytes)

        commit_time = _now_timestamp()
        stream_errors: list[types.StorageError] = []
        valid_streams: list[WriteStream] = []

        # Pass 1: validate every stream up-front. We cannot call
        # ``strategy.commit`` yet because it mutates state and clears the
        # buffer — doing that per-stream would leave partial state behind
        # if a later stream in the batch is invalid.
        for stream_name in request.write_streams:
            stream = self._manager.get(stream_name)
            if stream is None:
                stream_errors.append(
                    types.StorageError(
                        code=types.StorageError.StorageErrorCode.STREAM_NOT_FOUND,
                        entity=stream_name,
                        error_message=f"Stream not found: {stream_name}",
                    )
                )
                continue
            if stream.stream_type is not WriteStreamType.PENDING:
                stream_errors.append(
                    types.StorageError(
                        code=types.StorageError.StorageErrorCode.INVALID_STREAM_TYPE,
                        entity=stream_name,
                        error_message=(
                            "Only PENDING streams can be batch committed; "
                            f"got {stream.stream_type.value}"
                        ),
                    )
                )
                continue
            if stream.state is not WriteStreamState.FINALIZED:
                stream_errors.append(
                    types.StorageError(
                        code=types.StorageError.StorageErrorCode.INVALID_STREAM_STATE,
                        entity=stream_name,
                        error_message=(
                            "Stream must be finalized before BatchCommit; "
                            f"state={stream.state.value}"
                        ),
                    )
                )
                continue
            valid_streams.append(stream)

        if stream_errors:
            # Partial-failure semantics: real BigQuery returns an empty
            # commit_time and the error list. We don't flush anything if
            # any stream was invalid, and we don't mutate stream state.
            response = types.BatchCommitWriteStreamsResponse(
                commit_time=None,
                stream_errors=stream_errors,
            )
            return types.BatchCommitWriteStreamsResponse.serialize(response)

        # Pass 2: commit every stream's buffer and flush to DuckDB.
        for stream in valid_streams:
            outcome = select_strategy(stream.stream_type).commit(stream)
            if outcome.committed_rows is not None and outcome.committed_rows.num_rows > 0:
                self._flush_to_target(stream, outcome.committed_rows)

        response = types.BatchCommitWriteStreamsResponse(
            commit_time=commit_time,
            stream_errors=[],
        )
        return types.BatchCommitWriteStreamsResponse.serialize(response)

    # -- FlushRows ----------------------------------------------------------

    def _handle_flush_rows(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Publish BUFFERED stream rows up to a given offset."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.FlushRowsRequest.deserialize(request_bytes)
        stream = self._manager.get(request.write_stream)
        if stream is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Stream not found: {request.write_stream}")
            return b""
        if stream.stream_type is not WriteStreamType.BUFFERED:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(
                f"FlushRows only valid on BUFFERED streams; got {stream.stream_type.value}",
            )
            return b""

        strategy = select_strategy(stream.stream_type)
        outcome = strategy.flush(stream, request.offset)
        if not outcome.ok:
            context.set_code(grpc.StatusCode.OUT_OF_RANGE)
            context.set_details(outcome.detail)
            return b""

        if outcome.committed_rows is not None and outcome.committed_rows.num_rows > 0:
            self._flush_to_target(stream, outcome.committed_rows)

        response = types.FlushRowsResponse(offset=outcome.offset)
        return types.FlushRowsResponse.serialize(response)

    # -- AppendRows (bidirectional streaming) -------------------------------

    def _handle_append_rows(
        self,
        request_iterator: Iterator[bytes],
        context: grpc.ServicerContext,
    ) -> Iterator[bytes]:
        """Handle the bidirectional AppendRows stream.

        One decoder is cached per connection. The first request must
        include the writer_schema; subsequent requests can omit it.
        """
        from google.cloud.bigquery_storage_v1 import types

        proto_decoder: ProtoRowDecoder | None = None
        arrow_schema_bytes: bytes | None = None
        bound_stream: WriteStream | None = None
        target_schema: pa.Schema | None = None

        max_request_bytes = self._ctx.settings.write_api_max_request_bytes
        max_buffered_rows = self._ctx.settings.write_api_max_stream_rows

        for request_bytes in request_iterator:
            # Size guard — mirrors BigQuery's 10 MiB default. Reject before
            # deserialising so a malicious producer can't OOM the server
            # with a single giant Arrow/proto payload.
            if len(request_bytes) > max_request_bytes:
                yield _error_response(
                    bound_stream.name if bound_stream else "",
                    (
                        f"AppendRowsRequest exceeds the "
                        f"{max_request_bytes}-byte size cap "
                        f"({len(request_bytes)} bytes). "
                        "Split the payload into smaller batches."
                    ),
                    status_code=grpc.StatusCode.RESOURCE_EXHAUSTED,
                )
                continue

            request = types.AppendRowsRequest.deserialize(request_bytes)
            stream_name = request.write_stream

            # Resolve the stream on first message (or whenever it changes —
            # the real service requires a single stream per connection, but
            # we resolve defensively).
            if bound_stream is None or bound_stream.name != stream_name:
                bound_stream = self._resolve_append_stream(stream_name, context)
                if bound_stream is None:
                    return
                table_meta = self._ctx.catalog.get_table(
                    bound_stream.project_id,
                    bound_stream.dataset_id,
                    bound_stream.table_id,
                )
                if table_meta is None:
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details(
                        f"Table gone: {bound_stream.project_id}."
                        f"{bound_stream.dataset_id}.{bound_stream.table_id}",
                    )
                    return
                target_schema = _table_arrow_schema(table_meta)

            # bound_stream and target_schema are both set after the branch above.
            if bound_stream is None or target_schema is None:  # pragma: no cover
                return

            # Convert rows to a pyarrow.Table in the target schema.
            try:
                rows_table, proto_decoder, arrow_schema_bytes = _rows_for_request(
                    request,
                    proto_decoder,
                    arrow_schema_bytes,
                    target_schema,
                )
            except ValueError as exc:
                yield _error_response(bound_stream.name, str(exc))
                continue

            strategy = select_strategy(bound_stream.stream_type)
            # proto-plus auto-unwraps Int64Value → int; presence is tracked
            # on the underlying protobuf message — accessed via the
            # public ``proto.Message.pb()`` classmethod.
            offset = int(request.offset) if _has_offset(request) else None

            # Serialise AppendRows per-stream so a misbehaving client that
            # opens two connections on one stream can't race ``next_offset``
            # or ``buffer``. Real BigQuery documents that only one
            # connection may be open per stream; we enforce the invariant
            # defensively.
            with bound_stream.lock:
                outcome = strategy.append(
                    bound_stream,
                    rows_table,
                    offset,
                    max_buffered_rows=max_buffered_rows,
                )

                if outcome.status is not AppendStatus.OK:
                    yield _error_response(
                        bound_stream.name,
                        outcome.detail,
                        status_code=_proto_to_grpc_status(outcome.status),
                    )
                    continue

                if outcome.committed_rows is not None and outcome.committed_rows.num_rows > 0:
                    self._flush_to_target(bound_stream, outcome.committed_rows)

            yield _ok_response(bound_stream.name, outcome)

    # -- helpers -------------------------------------------------------------

    def _resolve_append_stream(
        self,
        stream_name: str,
        context: grpc.ServicerContext,
    ) -> WriteStream | None:
        """Look up (or auto-create) the stream referenced by an AppendRows request."""
        # Auto-create the default stream on first reference.
        if stream_name.endswith(f"/streams/{DEFAULT_STREAM_SUFFIX}"):
            try:
                project_id, dataset_id, table_id, _ = parse_stream_name(stream_name)
            except ValueError as exc:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(str(exc))
                return None
            if self._ctx.catalog.get_table(project_id, dataset_id, table_id) is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Table not found for default stream: {stream_name}")
                return None
            return self._manager.get_or_create_default(project_id, dataset_id, table_id)

        stream = self._manager.get(stream_name)
        if stream is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Stream not found: {stream_name}")
            return None
        return stream

    def _flush_to_target(self, stream: WriteStream, rows: pa.Table) -> None:
        """Insert ``rows`` into the target DuckDB table.

        Serialises under ``self._write_lock`` so concurrent AppendRows calls
        on different streams don't clash on the shared DuckDB register name
        or race to update ``num_rows``. All identifiers are validated via
        :mod:`bqemulator.storage.sql_identifiers` before interpolation —
        invalid ids would have been rejected on stream creation, but we
        defend-in-depth here since the stream state survives the RPC that
        created it.
        """
        target_ref = quoted_table_ref(
            stream.project_id,
            stream.dataset_id,
            stream.table_id,
        )
        reg_name = register_name(f"__bqemu_write_{uuid4().hex[:12]}")

        with self._write_lock:
            self._ctx.engine.connection.register(reg_name, rows)
            try:
                self._ctx.engine.execute(
                    f"INSERT INTO {target_ref} SELECT * FROM {reg_name}",
                )
            finally:
                self._ctx.engine.connection.unregister(reg_name)

            table_meta = self._ctx.catalog.get_table(
                stream.project_id,
                stream.dataset_id,
                stream.table_id,
            )
            if table_meta is not None:
                new_count = table_meta.num_rows + rows.num_rows
                self._ctx.catalog.update_table(
                    table_meta.model_copy(update={"num_rows": new_count}),
                )
            # Phase 7: capture a snapshot under the same write lock so
            # concurrent streams can't interleave a half-applied rebase.
            # ``record_change`` publishes ``TableDataChanged``, which
            # both invalidates the query cache and marks dependent MVs
            # stale.
            self._ctx.snapshots.record_change(
                stream.project_id,
                stream.dataset_id,
                stream.table_id,
            )


# ---------------------------------------------------------------------------
# Module-level helpers (kept outside the handler so they remain stateless)
# ---------------------------------------------------------------------------


def _rows_for_request(
    request: Any,
    proto_decoder: ProtoRowDecoder | None,
    arrow_schema_bytes: bytes | None,
    target_schema: pa.Schema,
) -> tuple[pa.Table, ProtoRowDecoder | None, bytes | None]:
    """Extract rows from an AppendRows request, sticky-caching decoders.

    Returns ``(rows_table, updated_proto_decoder, updated_arrow_schema_bytes)``.
    """
    # Proto path.
    if _has_field(request, "proto_rows"):
        proto_data = request.proto_rows
        # Build (or refresh) the decoder if the writer_schema is present.
        if _has_field(proto_data, "writer_schema"):
            # ``writer_schema.proto_descriptor`` is already a raw protobuf
            # ``DescriptorProto`` (not a proto-plus wrapper), so we pass it
            # straight into the decoder.
            proto_decoder = ProtoRowDecoder(proto_data.writer_schema.proto_descriptor)
        if proto_decoder is None:
            raise ValueError(
                "proto_rows sent without prior writer_schema on this connection",
            )
        serialized_rows = list(proto_data.rows.serialized_rows)
        rows_table = proto_rows_to_arrow_table(
            proto_decoder,
            serialized_rows,
            target_schema,
        )
        return rows_table, proto_decoder, arrow_schema_bytes

    # Arrow path.
    if _has_field(request, "arrow_rows"):
        arrow_data = request.arrow_rows
        if _has_field(arrow_data, "writer_schema"):
            arrow_schema_bytes = bytes(arrow_data.writer_schema.serialized_schema)
        if arrow_schema_bytes is None:
            raise ValueError(
                "arrow_rows sent without prior writer_schema on this connection",
            )
        record_batch = bytes(arrow_data.rows.serialized_record_batch)
        rows_table = deserialize_arrow_rows(arrow_schema_bytes, record_batch)
        # Ensure the columns line up with the target table schema. Missing
        # columns become NULL; extra columns are ignored (parity with BQ's
        # "best-effort" coercion).
        rows_table = _align_to_target(rows_table, target_schema)
        return rows_table, proto_decoder, arrow_schema_bytes

    # Empty append (no rows): used for keep-alive or to verify the stream.
    empty = _empty_table(target_schema)
    return empty, proto_decoder, arrow_schema_bytes


def _align_to_target(table: pa.Table, target_schema: pa.Schema) -> pa.Table:
    """Reshape an Arrow table so its columns match ``target_schema``."""
    import pyarrow as pa

    arrays: list[pa.Array] = []
    for field in target_schema:
        if field.name in table.column_names:
            col = table.column(field.name)
            if col.type == field.type:
                arrays.append(col.combine_chunks())
            else:
                arrays.append(col.cast(field.type).combine_chunks())
        else:
            arrays.append(pa.nulls(table.num_rows, type=field.type))
    return pa.Table.from_arrays(arrays, schema=target_schema)


def _empty_table(schema: pa.Schema) -> pa.Table:
    import pyarrow as pa

    return pa.table({f.name: pa.array([], type=f.type) for f in schema})


def _underlying_pb(message: Any) -> Any | None:
    """Return the wrapped google.protobuf message for a proto-plus object.

    Proto-plus exposes :py:meth:`proto.Message.pb` as the public accessor
    (see https://proto-plus-python.readthedocs.io/). We keep this helper
    so tests can swap in plain protobuf messages without proto-plus.
    """
    import proto as proto_plus

    if isinstance(message, proto_plus.Message):
        return type(message).pb(message)
    if hasattr(message, "HasField"):
        return message
    return None


def _has_field(message: Any, field_name: str) -> bool:
    """Return True iff ``field_name`` is present on ``message``.

    Works for proto-plus-wrapped messages and raw google.protobuf
    messages. Falls back to truthy-attr probing for non-message fields.
    """
    pb = _underlying_pb(message)
    if pb is not None:
        try:
            return pb.HasField(field_name)
        except ValueError:
            pass
    return getattr(message, field_name, None) is not None


def _has_offset(request: Any) -> bool:
    """Return True iff the AppendRowsRequest explicitly set ``offset``."""
    pb = _underlying_pb(request)
    if pb is None:
        return False
    try:
        return pb.HasField("offset")
    except ValueError:
        return False


def _ok_response(stream_name: str, outcome: AppendOutcome) -> bytes:
    from google.cloud.bigquery_storage_v1 import types

    # proto-plus auto-wraps the Int64Value-typed offset from a plain int.
    append_result = types.AppendRowsResponse.AppendResult(offset=outcome.offset)
    response = types.AppendRowsResponse(
        append_result=append_result,
        write_stream=stream_name,
    )
    return types.AppendRowsResponse.serialize(response)


def _error_response(
    stream_name: str,
    detail: str,
    status_code: grpc.StatusCode = grpc.StatusCode.INVALID_ARGUMENT,
) -> bytes:
    """Return an AppendRowsResponse with the error field populated.

    Client libraries surface this as ``AppendRowsResponse.error`` — the
    connection stays open so subsequent rows can still be sent.
    """
    from google.cloud.bigquery_storage_v1 import types
    from google.rpc import status_pb2

    response = types.AppendRowsResponse(
        write_stream=stream_name,
        error=status_pb2.Status(
            code=status_code.value[0],
            message=detail,
        ),
    )
    return types.AppendRowsResponse.serialize(response)


def _now_timestamp() -> Any:
    """Return a ``google.protobuf.Timestamp`` for the current wall clock."""
    from google.protobuf.timestamp_pb2 import Timestamp

    ts = Timestamp()
    ts.FromDatetime(datetime.now(tz=UTC).replace(tzinfo=None))
    return ts


def _is_valid_stream_name(name: str) -> bool:
    """Return True when ``name`` matches the canonical stream-name shape.

    Real BigQuery rejects malformed stream names with INVALID_ARGUMENT
    rather than NOT_FOUND — the wire-shape distinction matters for
    clients that retry NOT_FOUND but error-out on INVALID_ARGUMENT.
    The canonical shape is
    ``projects/<p>/datasets/<d>/tables/<t>/streams/<id>`` where ``id``
    is either a 16-hex-char unique token or the ``_default`` sentinel.
    """
    try:
        parts = name.split("/")
        expected_parts = 8
        if len(parts) != expected_parts or parts[0] != "projects" or parts[6] != "streams":
            return False
        stream_id = parts[7]
    except (IndexError, AttributeError):
        return False
    return bool(_STREAM_ID_RE.match(stream_id))


def _proto_type_to_strategy(proto_type: int) -> WriteStreamType | None:
    from google.cloud.bigquery_storage_v1 import types

    if proto_type == int(types.WriteStream.Type.COMMITTED):
        return WriteStreamType.COMMITTED
    if proto_type == int(types.WriteStream.Type.PENDING):
        return WriteStreamType.PENDING
    if proto_type == int(types.WriteStream.Type.BUFFERED):
        return WriteStreamType.BUFFERED
    # DEFAULT streams cannot be explicitly created — they are implicit.
    return None


def _strategy_type_to_proto(stream_type: WriteStreamType) -> int:
    from google.cloud.bigquery_storage_v1 import types

    # Real BigQuery reports the implicit DEFAULT stream as a COMMITTED
    # stream over the wire — clients dispatch on the type and treat
    # both the same way. The wire-format conformance suite (P3.d)
    # asserts this contract.
    mapping = {
        WriteStreamType.DEFAULT: int(types.WriteStream.Type.COMMITTED),
        WriteStreamType.COMMITTED: int(types.WriteStream.Type.COMMITTED),
        WriteStreamType.PENDING: int(types.WriteStream.Type.PENDING),
        WriteStreamType.BUFFERED: int(types.WriteStream.Type.BUFFERED),
    }
    return mapping[stream_type]


def _table_meta_to_proto_schema(table_meta: TableMeta) -> Any:
    """Convert a :class:`TableMeta` to a BigQuery Storage TableSchema proto."""
    from google.cloud.bigquery_storage_v1 import types

    fields = [
        types.TableFieldSchema(
            name=field.name,
            type_=_bq_type_to_storage_type(field.type),
            mode=_bq_mode_to_storage_mode(field.mode),
        )
        for field in table_meta.schema_.fields
    ]
    return types.TableSchema(fields=fields)


def _bq_type_to_storage_type(bq_type: str) -> int:
    from google.cloud.bigquery_storage_v1 import types

    type_map: dict[str, Any] = {
        "STRING": types.TableFieldSchema.Type.STRING,
        "INT64": types.TableFieldSchema.Type.INT64,
        "INTEGER": types.TableFieldSchema.Type.INT64,
        "FLOAT64": types.TableFieldSchema.Type.DOUBLE,
        "FLOAT": types.TableFieldSchema.Type.DOUBLE,
        "BOOL": types.TableFieldSchema.Type.BOOL,
        "BOOLEAN": types.TableFieldSchema.Type.BOOL,
        "BYTES": types.TableFieldSchema.Type.BYTES,
        "TIMESTAMP": types.TableFieldSchema.Type.TIMESTAMP,
        "DATE": types.TableFieldSchema.Type.DATE,
        "TIME": types.TableFieldSchema.Type.TIME,
        "DATETIME": types.TableFieldSchema.Type.DATETIME,
        "NUMERIC": types.TableFieldSchema.Type.NUMERIC,
        "BIGNUMERIC": types.TableFieldSchema.Type.BIGNUMERIC,
        "JSON": types.TableFieldSchema.Type.JSON,
    }
    return int(type_map.get(bq_type.upper(), types.TableFieldSchema.Type.STRING))


def _bq_mode_to_storage_mode(mode: str) -> int:
    from google.cloud.bigquery_storage_v1 import types

    mapping = {
        "NULLABLE": types.TableFieldSchema.Mode.NULLABLE,
        "REQUIRED": types.TableFieldSchema.Mode.REQUIRED,
        "REPEATED": types.TableFieldSchema.Mode.REPEATED,
    }
    return int(mapping.get(mode, types.TableFieldSchema.Mode.NULLABLE))


__all__ = ["BigQueryWriteHandler"]
