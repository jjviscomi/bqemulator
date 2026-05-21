"""Write stream state and manager for the Storage Write API.

Every ``BigQueryWrite`` service call operates on a :class:`WriteStreamState`
keyed by its fully-qualified stream name. Per ADR 0013, stream state is
held in an in-memory dictionary — sufficient for single-process emulator
semantics and matching the ephemeral-by-default persistence model.

Stream types (real BigQuery parity):

* ``DEFAULT`` — implicit, always-exists stream per table. Each AppendRows
  is immediately committed; no offset dedup.
* ``COMMITTED`` — explicit stream. Each AppendRows is immediately committed;
  offsets are strictly monotonic starting at 0 (duplicates → ALREADY_EXISTS,
  gaps → OUT_OF_RANGE).
* ``PENDING`` — explicit stream. AppendRows buffers rows in memory. Rows
  become visible only when the stream is ``FinalizeWriteStream`` + included
  in a ``BatchCommitWriteStreams`` call.
* ``BUFFERED`` — explicit stream. AppendRows buffers rows. ``FlushRows``
  with an offset makes rows up to and including that offset visible.

State machine:

    CREATED ──AppendRows──► CREATED
       │
       ├── FinalizeWriteStream ──► FINALIZED   (for PENDING/COMMITTED/BUFFERED)
       │                               │
       │                               └── BatchCommit ──► COMMITTED  (PENDING only)
       │
       └── DEFAULT streams never transition — they are logically always open.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa


class WriteStreamType(StrEnum):
    """Kind of write stream; maps 1:1 to BigQuery ``WriteStream.Type``."""

    DEFAULT = "DEFAULT"
    COMMITTED = "COMMITTED"
    PENDING = "PENDING"
    BUFFERED = "BUFFERED"


class WriteStreamState(StrEnum):
    """Lifecycle state of a write stream."""

    OPEN = "OPEN"
    FINALIZED = "FINALIZED"
    COMMITTED = "COMMITTED"  # PENDING streams only, after BatchCommit


DEFAULT_STREAM_SUFFIX = "_default"


@dataclass(slots=True)
class WriteStream:
    """In-memory state for a single write stream."""

    name: str
    project_id: str
    dataset_id: str
    table_id: str
    stream_type: WriteStreamType
    state: WriteStreamState = WriteStreamState.OPEN

    # For COMMITTED/PENDING/BUFFERED: next expected offset (strict order).
    next_offset: int = 0

    # For PENDING/BUFFERED: accumulated rows as pyarrow Tables, not yet
    # visible in the target table. Empty for DEFAULT/COMMITTED.
    buffer: list[pa.Table] = field(default_factory=list)

    # For BUFFERED: highest row-index (exclusive) that has been flushed.
    flushed_rows: int = 0

    # Total rows ever appended (FinalizeWriteStreamResponse.row_count).
    row_count: int = 0

    # Per-stream append mutex. BigQuery documents that only one AppendRows
    # connection may be open per stream at a time; we enforce that so
    # misbehaving clients can't race ``next_offset``/``buffer``.
    lock: RLock = field(default_factory=RLock)

    # Whether a CreateWriteStream-increment of ``write_streams_active`` has
    # been paired with a Finalize-decrement. Used to clean up the gauge
    # when a stream is deleted/expired without going through Finalize.
    metric_registered: bool = False


def build_stream_name(
    project_id: str,
    dataset_id: str,
    table_id: str,
    stream_id: str,
) -> str:
    """Return the canonical stream name."""
    return f"projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/streams/{stream_id}"


def default_stream_name(project_id: str, dataset_id: str, table_id: str) -> str:
    """Return the canonical name for a table's implicit DEFAULT stream."""
    return build_stream_name(project_id, dataset_id, table_id, DEFAULT_STREAM_SUFFIX)


def parse_stream_name(name: str) -> tuple[str, str, str, str]:
    """Split a stream name into (project, dataset, table, stream_id).

    Raises :class:`ValueError` if the name doesn't match the expected
    ``projects/.../streams/...`` shape.
    """
    parts = name.split("/")
    expected_parts = 8
    if len(parts) != expected_parts or parts[0] != "projects" or parts[6] != "streams":
        raise ValueError(f"Invalid stream name: {name}")
    return parts[1], parts[3], parts[5], parts[7]


def parse_table_parent(parent: str) -> tuple[str, str, str]:
    """Split a ``CreateWriteStream`` parent into (project, dataset, table).

    The parent follows the ``projects/{p}/datasets/{d}/tables/{t}`` form.
    """
    parts = parent.split("/")
    expected_parts = 6
    if len(parts) != expected_parts or parts[0] != "projects":
        raise ValueError(f"Invalid table parent: {parent}")
    return parts[1], parts[3], parts[5]


class WriteStreamManager:
    """Thread-safe in-memory store for :class:`WriteStream` objects.

    The manager is shared across the gRPC handler for the life of the
    process. All mutating methods serialize on an internal ``RLock`` so
    concurrent AppendRows calls to different streams don't corrupt state.

    The ``on_remove`` callback is invoked whenever a stream is removed
    (via ``delete`` or ``clear``) so the caller can balance metric
    increments tied to stream creation.
    """

    def __init__(
        self,
        *,
        on_remove: Callable[[WriteStream], None] | None = None,
    ) -> None:
        self._streams: dict[str, WriteStream] = {}
        self._lock = RLock()
        self._on_remove = on_remove

    def set_on_remove(self, callback: Callable[[WriteStream], None]) -> None:
        """Install the removal callback after construction.

        The composition root constructs the manager before the gRPC
        servicer can supply the metric-cleanup callback (the servicer
        needs the AppContext, which needs the manager). This lets the
        servicer wire the callback in once both objects exist.
        """
        self._on_remove = callback

    def list_active(self) -> tuple[WriteStream, ...]:
        """Return a snapshot of every stream currently tracked.

        The returned tuple is a copy — concurrent mutations to the
        manager will not affect callers that iterate the result. Used by
        the /admin/streams endpoint for diagnostics.
        """
        with self._lock:
            return tuple(self._streams.values())

    def create(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        stream_id: str,
        stream_type: WriteStreamType,
    ) -> WriteStream:
        """Create a new write stream. Raises ``ValueError`` on duplicate id."""
        name = build_stream_name(project_id, dataset_id, table_id, stream_id)
        with self._lock:
            if name in self._streams:
                raise ValueError(f"Stream already exists: {name}")
            stream = WriteStream(
                name=name,
                project_id=project_id,
                dataset_id=dataset_id,
                table_id=table_id,
                stream_type=stream_type,
            )
            self._streams[name] = stream
            return stream

    def get_or_create_default(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> WriteStream:
        """Return the implicit DEFAULT stream, creating it on first access."""
        name = default_stream_name(project_id, dataset_id, table_id)
        with self._lock:
            existing = self._streams.get(name)
            if existing is not None:
                return existing
            stream = WriteStream(
                name=name,
                project_id=project_id,
                dataset_id=dataset_id,
                table_id=table_id,
                stream_type=WriteStreamType.DEFAULT,
            )
            self._streams[name] = stream
            return stream

    def get(self, name: str) -> WriteStream | None:
        """Return the stream for ``name`` or ``None`` if unknown."""
        with self._lock:
            return self._streams.get(name)

    def delete(self, name: str) -> None:
        """Forget a stream. Fires ``on_remove`` for metric cleanup."""
        with self._lock:
            removed = self._streams.pop(name, None)
        if removed is not None and self._on_remove is not None:
            self._on_remove(removed)

    def clear(self) -> None:
        """Remove all streams. Fires ``on_remove`` for each."""
        with self._lock:
            removed = list(self._streams.values())
            self._streams.clear()
        if self._on_remove is not None:
            for stream in removed:
                self._on_remove(stream)


__all__ = [
    "DEFAULT_STREAM_SUFFIX",
    "WriteStream",
    "WriteStreamManager",
    "WriteStreamState",
    "WriteStreamType",
    "build_stream_name",
    "default_stream_name",
    "parse_stream_name",
    "parse_table_parent",
]
