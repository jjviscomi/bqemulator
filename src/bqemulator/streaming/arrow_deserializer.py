"""Arrow row-format deserializer for Storage Write API.

Real BigQuery's ``AppendRowsRequest.arrow_rows`` carries:

* ``writer_schema.serialized_schema`` — an Arrow IPC stream containing only
  the schema.
* ``rows.serialized_record_batch`` — one Arrow IPC record batch (no schema
  header).

To reconstruct a :class:`pyarrow.Table` we prepend the schema bytes to
the batch bytes and feed them to :func:`pyarrow.ipc.open_stream`.
"""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.ipc


def deserialize_arrow_rows(
    serialized_schema: bytes,
    serialized_record_batch: bytes,
) -> pa.Table:
    """Reconstruct a :class:`pyarrow.Table` from a write-API Arrow payload.

    Args:
        serialized_schema: The Arrow IPC schema bytes (from
            ``AppendRowsRequest.arrow_rows.writer_schema.serialized_schema``).
        serialized_record_batch: One Arrow IPC record batch (from
            ``AppendRowsRequest.arrow_rows.rows.serialized_record_batch``).

    Returns:
        A :class:`pyarrow.Table` containing the appended rows.

    Raises:
        ValueError: If the payload cannot be parsed.
    """
    if not serialized_schema:
        raise ValueError("Arrow writer_schema is empty")
    if not serialized_record_batch:
        # Zero-row append is legal; return an empty table derived from schema.
        # pyarrow 24.x stubs mark ``pa.ipc.open_stream`` as untyped; earlier
        # versions ship a proper signature. ``unused-ignore`` keeps the
        # suppression cross-version-compatible.
        reader = pa.ipc.open_stream(serialized_schema)  # type: ignore[no-untyped-call,unused-ignore]
        return reader.read_all()

    stream_bytes = serialized_schema + serialized_record_batch
    try:
        reader = pa.ipc.open_stream(io.BytesIO(stream_bytes))  # type: ignore[no-untyped-call,unused-ignore]
        return reader.read_all()
    except pa.ArrowInvalid as exc:
        raise ValueError(f"Failed to parse Arrow record batch: {exc}") from exc


__all__ = ["deserialize_arrow_rows"]
