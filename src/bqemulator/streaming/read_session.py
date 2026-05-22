"""Read session manager.

Manages the lifecycle of BigQuery Storage Read API sessions:

1. **CreateReadSession**: Execute the query, materialize result as a
   ``pyarrow.Table``, split into N streams by row range.
2. **ReadRows**: Serialize a stream's slice as Arrow IPC batches or
   naked Avro binary rows, depending on the session's ``data_format``.
3. **SplitReadStream**: Subdivide a stream into two halves; the
   child streams inherit the parent's ``data_format``.

Per ADR 0008, materialization at session creation IS the snapshot —
subsequent writes to the source table do not affect in-flight sessions.

Per ADR 0030, the chosen wire format (Arrow IPC vs Apache Avro) is
fixed at session creation. ``ReadRows`` reads it off the
:class:`ReadSessionState` so subsequent calls on child streams use
the same format without re-deriving it from the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc

from bqemulator.observability.logging_ import get_logger

_log = get_logger(__name__)


# Wire-format identifiers stored on :class:`ReadSessionState`. Match
# the values of ``google.cloud.bigquery_storage_v1.types.DataFormat``
# (ARROW=2, AVRO=1) so the gRPC servicer can compare without
# importing the proto types into this module.
FORMAT_ARROW = "ARROW"
FORMAT_AVRO = "AVRO"


@dataclass(slots=True)
class ReadStream:
    """A single stream within a read session."""

    name: str
    start_row: int
    end_row: int  # exclusive


@dataclass(slots=True)
class ReadSessionState:
    """In-memory state for a read session."""

    session_name: str
    table: pa.Table
    arrow_schema_bytes: bytes
    streams: list[ReadStream] = field(default_factory=list)
    data_format: str = FORMAT_ARROW
    # Avro JSON schema string (only populated for AVRO sessions).
    # Computed once at session creation so every ReadRows + child
    # stream serves the same schema without re-deriving it.
    avro_schema_json: str = ""


# In-memory session store.
_SESSIONS: dict[str, ReadSessionState] = {}

# Real BigQuery returns 1 stream regardless of ``max_stream_count``
# when the underlying table is under this size — empirically ~1 MB.
# Matched by :func:`create_read_session` so the wire-format
# conformance suite (P3.d) holds for small fixtures.
_SMALL_TABLE_BYTE_THRESHOLD = 1_000_000


def create_read_session(
    project_id: str,
    table_ref: str,  # noqa: ARG001 — used for session naming context in future
    arrow_table: pa.Table,
    max_streams: int = 1,
    selected_fields: list[str] | None = None,
    data_format: str = FORMAT_ARROW,
    required_field_names: frozenset[str] | None = None,
) -> ReadSessionState:
    """Create a new read session by materializing the query result.

    Args:
        project_id: The project the session belongs to.
        table_ref: The source table reference (for naming).
        arrow_table: The pre-materialized query result.
        max_streams: Maximum number of parallel streams to create.
        selected_fields: Optional column projection.
        data_format: Wire format the session emits (``FORMAT_ARROW``
            or ``FORMAT_AVRO``). The Avro path computes a JSON
            schema once at creation time and stores it on the state
            so :func:`ReadRows` and split-stream children can emit
            the same schema without re-deriving it.
        required_field_names: Names of source-table columns flagged
            ``mode='REQUIRED'`` in the BigQuery catalog. Used by the
            Avro path to emit a bare ``T`` type for those columns
            (real BigQuery's shape) instead of the ``["null", T]``
            union DuckDB's nullable-by-default query schema would
            otherwise produce.

    Returns:
        A :class:`ReadSessionState` with the materialized data and
        stream assignments.
    """
    # Apply column projection if requested.
    if selected_fields:
        available = set(arrow_table.column_names)
        cols = [c for c in selected_fields if c in available]
        if cols:
            arrow_table = arrow_table.select(cols)

    session_id = uuid4().hex[:16]
    session_name = f"projects/{project_id}/locations/US/sessions/{session_id}"

    # Split rows across streams. Real BigQuery treats
    # ``max_stream_count`` as an upper bound and returns fewer streams
    # when the table is too small to benefit from parallel reads —
    # empirically, tables under ~1 MB get 1 stream regardless. Match
    # that contract so the gRPC-corpus wire-format diff (P3.d) holds.
    num_rows = arrow_table.num_rows
    table_bytes = arrow_table.nbytes
    requested = max(max_streams, 1)
    if table_bytes < _SMALL_TABLE_BYTE_THRESHOLD:
        # Small-table cap: 1 stream regardless of max_stream_count.
        effective_streams = 1 if num_rows > 0 else 0
    else:
        effective_streams = min(requested, 10, max(num_rows, 1))
    chunk_size = max(num_rows // effective_streams, 1) if effective_streams > 0 else 0

    streams: list[ReadStream] = []
    for i in range(effective_streams):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < effective_streams - 1 else num_rows
        if start >= num_rows:
            break
        stream_name = f"{session_name}/streams/{i}"
        streams.append(ReadStream(name=stream_name, start_row=start, end_row=end))

    # pyarrow 24.x stubs mark ``pa.ipc.new_stream`` as untyped; earlier
    # versions ship a proper signature. ``unused-ignore`` keeps the
    # suppression cross-version-compatible.
    schema_sink = io.BytesIO()
    writer = pa.ipc.new_stream(schema_sink, arrow_table.schema)  # type: ignore[no-untyped-call,unused-ignore]
    writer.close()
    schema_bytes = schema_sink.getvalue()

    # Pre-compute the Avro schema JSON for AVRO sessions so every
    # ReadRows call (and any split-stream child) serves the same
    # schema without re-deriving it. Arrow sessions skip this.
    avro_schema_json = ""
    if data_format == FORMAT_AVRO:
        from bqemulator.streaming.avro_serializer import arrow_schema_to_avro_json

        avro_schema_json = arrow_schema_to_avro_json(
            arrow_table.schema,
            required_field_names=required_field_names,
        )

    state = ReadSessionState(
        session_name=session_name,
        table=arrow_table,
        arrow_schema_bytes=schema_bytes,
        streams=streams,
        data_format=data_format,
        avro_schema_json=avro_schema_json,
    )
    _SESSIONS[session_name] = state

    _log.info(
        "read_session.created",
        session=session_name,
        num_rows=num_rows,
        num_streams=len(streams),
        data_format=data_format,
    )
    return state


def get_session(session_name: str) -> ReadSessionState | None:
    """Look up a session by name."""
    return _SESSIONS.get(session_name)


def get_stream_data(
    session_name: str,
    stream_name: str,
) -> pa.Table | None:
    """Return the Arrow table slice for a specific stream.

    Returns ``None`` if the session or stream is not found.
    """
    state = _SESSIONS.get(session_name)
    if state is None:
        return None

    for stream in state.streams:
        if stream.name == stream_name:
            return state.table.slice(
                stream.start_row,
                stream.end_row - stream.start_row,
            )
    return None


def split_stream(stream_name: str, fraction: float) -> tuple[str, str] | None:
    """Split a stream at ``fraction``, returning two new stream names.

    Mints two fresh stream entries — primary covers
    ``[start_row, split_row)`` and remainder covers ``[split_row, end_row)``.
    Both are registered with the owning session so subsequent
    ``ReadRows`` calls route through :func:`get_stream_data`. Returns
    ``None`` if the stream is not found. The wire-format conformance
    suite (P3.d) asserts that ``SplitReadStream`` returns a non-empty
    response shape; the emulator does not optimise parallelism (the
    BQ compatibility matrix flags this as a hint-only surface).
    """
    session_name = "/".join(stream_name.split("/")[:-2])
    state = _SESSIONS.get(session_name)
    if state is None:
        return None
    target_index: int | None = None
    for idx, stream in enumerate(state.streams):
        if stream.name == stream_name:
            target_index = idx
            break
    if target_index is None:
        return None
    target = state.streams[target_index]
    width = target.end_row - target.start_row
    split_row = target.start_row + max(0, min(width, int(width * fraction)))
    primary_name = f"{session_name}/streams/p{uuid4().hex[:8]}"
    remainder_name = f"{session_name}/streams/r{uuid4().hex[:8]}"
    primary = ReadStream(name=primary_name, start_row=target.start_row, end_row=split_row)
    remainder = ReadStream(name=remainder_name, start_row=split_row, end_row=target.end_row)
    state.streams.append(primary)
    state.streams.append(remainder)
    return primary_name, remainder_name


def _type_contains_dictionary(arrow_type: pa.DataType) -> bool:
    """Return ``True`` if ``arrow_type`` or any nested child is dict-encoded.

    ``pa.types.is_dictionary`` only inspects the top-level type;
    Arrow's IPC format emits ``DictionaryBatch`` frames for dict
    children inside struct / list / map / union containers as well.
    See ADR 0033.
    """
    if pa.types.is_dictionary(arrow_type):
        return True
    if pa.types.is_struct(arrow_type):
        return any(_type_contains_dictionary(f.type) for f in arrow_type)
    if (
        pa.types.is_list(arrow_type)
        or pa.types.is_large_list(arrow_type)
        or pa.types.is_fixed_size_list(arrow_type)
    ):
        return _type_contains_dictionary(arrow_type.value_type)
    if pa.types.is_map(arrow_type):
        return _type_contains_dictionary(arrow_type.key_type) or _type_contains_dictionary(
            arrow_type.item_type
        )
    if pa.types.is_union(arrow_type):
        return any(_type_contains_dictionary(f.type) for f in arrow_type)
    return False


def serialize_arrow_record_batch(batch: pa.RecordBatch) -> bytes:
    """Serialize a single Arrow record batch as a bare IPC message.

    Returns ONLY the record-batch IPC message bytes (continuation
    marker + metadata length + flatbuffer metadata + padding + body
    buffers), NOT a full IPC stream. This matches the BigQuery
    Storage Read API contract: ``ArrowRecordBatch.serialized_record_batch``
    is documented to carry a single batch message; the schema travels
    separately via ``ReadSession.arrow_schema.serialized_schema`` and
    the first ``ReadRowsResponse.arrow_schema`` field.

    Earlier (≤ v1.0.0) the read servicer packed a full IPC stream
    (schema framing + batches + EOS marker) into ``serialized_record_batch``.
    Real Storage Read clients tripped on the format mismatch — e.g.
    ``google-cloud-bigquery-storage``'s ``reader.to_arrow(session)``
    calls ``pyarrow.ipc.read_record_batch(...)`` which raises
    ``OSError: Expected IPC message of type record batch but got schema``.
    This function emits the documented format so those clients work
    unchanged. See issue #15.

    The implementation writes the batch through ``pa.ipc.new_stream``
    to a transient buffer, then re-reads the stream with a
    ``MessageReader`` to peel off the schema-message prefix and the
    EOS-marker suffix, leaving just the batch-message bytes. This is
    the portable approach across pyarrow versions; the alternative
    (low-level ``pa.ipc.write_message``) has signature drift between
    pyarrow 14.x and 17.x+ that we'd rather not chase.

    Dictionary-encoded columns are explicitly rejected. The
    BigQuery Storage Read wire format only carries
    ``ArrowSchema.serialized_schema`` and
    ``ArrowRecordBatch.serialized_record_batch`` — there's no slot
    for ``DictionaryBatch`` frames. pyarrow's
    ``read_record_batch(bytes, schema)`` requires a populated
    ``DictionaryMemo`` to decode dict-encoded fields, which the
    bare-message contract can't provide; quietly stripping the
    ``DictionaryBatch`` frames would produce a wire-format message
    consumers can't decode. Raise ``ValueError`` at the producer
    boundary so the misuse fails loudly. Real BigQuery doesn't
    surface dict-encoded columns through Storage Read either —
    if a future code path needs them, plumb a separate channel
    for the dictionary frames or use a different wire format.

    The loop below also skips any unexpected non-RecordBatch
    message types pyarrow could emit (compression headers,
    sentinel markers in future format versions, …) so the
    function stays correct across pyarrow upgrades.
    """
    # Refuse dict-encoded batches up front — see docstring.
    # Walks every type recursively because ``pa.types.is_dictionary``
    # only inspects the top-level type, and Arrow's IPC format emits
    # ``DictionaryBatch`` frames for dict-encoded children inside
    # struct / list / map / union containers as well. A flat check
    # would silently allow nested dict fields and produce the same
    # corrupt-payload bug the rejection guards against. Using
    # ``schema_field`` instead of ``field`` to avoid shadowing the
    # ``dataclasses.field`` import at the top of the module.
    for schema_field in batch.schema:
        if _type_contains_dictionary(schema_field.type):
            raise ValueError(
                f"Dictionary-encoded columns are not supported by"
                f" the BigQuery Storage Read API serializer "
                f"(column {schema_field.name!r} has type "
                f"{schema_field.type}; dict-encoded leaf detected"
                f" at the top level or in a nested container). The"
                f" wire contract carries only the record-batch"
                f" message, but pyarrow requires a DictionaryMemo"
                f" to decode dict frames — that channel does not"
                f" exist in the wire format. See ADR 0033."
            )

    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, batch.schema)  # type: ignore[no-untyped-call,unused-ignore]
    writer.write_batch(batch)
    writer.close()
    stream_bytes = sink.getvalue()

    # Stream layout (typed schemas without dict columns):
    #   [schema-message][batch-message][EOS-marker]
    # Stream layout (with dict-encoded columns):
    #   [schema][dict-1]...[dict-N][batch][EOS]
    # Either way we want the RecordBatch message — loop until we
    # find one. ``MessageReader`` is re-exported by the
    # ``pyarrow.ipc`` module but the stubs don't list it; the
    # ignore matches the pattern used elsewhere in this file for
    # ``new_stream``.
    reader = pa.ipc.MessageReader.open_stream(  # type: ignore[attr-defined]
        pa.BufferReader(stream_bytes)
    )
    batch_msg = None
    try:
        while True:
            msg = reader.read_next_message()
            if msg.type == "record batch":
                batch_msg = msg
                break
            # Otherwise (schema / dictionary / ...), skip and
            # keep reading.
    except StopIteration:
        # End-of-stream without ever seeing a RecordBatch is a
        # writer-side bug — defend against it explicitly rather
        # than returning empty bytes downstream.
        pass
    if batch_msg is None:
        raise RuntimeError(
            "pyarrow IPC stream contained no RecordBatch message",
        )

    out = pa.BufferOutputStream()
    batch_msg.serialize_to(out)
    return bytes(out.getvalue())


__all__ = [
    "FORMAT_ARROW",
    "FORMAT_AVRO",
    "ReadSessionState",
    "ReadStream",
    "create_read_session",
    "get_session",
    "get_stream_data",
    "serialize_arrow_record_batch",
    "split_stream",
]
