"""Strategy protocol and outcome types for Storage Write API streams."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from bqemulator.streaming.write_stream import WriteStreamType

if TYPE_CHECKING:
    import pyarrow as pa

    from bqemulator.streaming.write_stream import WriteStream


class AppendStatus(StrEnum):
    """Result of an AppendRows invocation."""

    OK = "OK"
    ALREADY_EXISTS = "ALREADY_EXISTS"  # duplicate offset
    OUT_OF_RANGE = "OUT_OF_RANGE"  # gap or non-monotonic offset
    STREAM_FINALIZED = "STREAM_FINALIZED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"  # buffer cap exceeded


@dataclass(slots=True, frozen=True)
class AppendOutcome:
    """Outcome of :py:meth:`WriteStrategy.append`.

    ``committed_rows`` is the pyarrow table that should be written to the
    target table *in the current RPC* (may be empty for buffered strategies).
    ``offset`` is the offset echoed back to the client in the response.
    """

    status: AppendStatus
    committed_rows: pa.Table | None = None
    offset: int = 0
    detail: str = ""


@dataclass(slots=True, frozen=True)
class FlushOutcome:
    """Outcome of :py:meth:`WriteStrategy.flush` (BUFFERED streams)."""

    ok: bool
    offset: int = 0
    committed_rows: pa.Table | None = None
    detail: str = ""


@dataclass(slots=True, frozen=True)
class CommitOutcome:
    """Outcome of :py:meth:`WriteStrategy.commit` (PENDING streams)."""

    ok: bool
    committed_rows: pa.Table | None = None
    detail: str = ""


class WriteStrategy(Protocol):
    """Strategy interface for handling writes against a write stream."""

    stream_type: WriteStreamType

    def append(
        self,
        stream: WriteStream,
        rows: pa.Table,
        offset: int | None,
        *,
        max_buffered_rows: int | None = None,
    ) -> AppendOutcome:
        """Handle an ``AppendRows`` invocation.

        Args:
            stream: The stream to append to.
            rows: The rows to append.
            offset: Optional client-supplied offset.
            max_buffered_rows: Optional cap on the buffered row count for
                PENDING/BUFFERED streams. A ``None`` value means no cap.
                Ignored by DEFAULT/COMMITTED (they never buffer).

        Returns an :class:`AppendOutcome`; the caller is responsible for
        actually writing ``outcome.committed_rows`` to the DuckDB target.
        """
        ...

    def flush(self, stream: WriteStream, offset: int) -> FlushOutcome:
        """Handle a ``FlushRows`` invocation (BUFFERED only)."""
        ...

    def commit(self, stream: WriteStream) -> CommitOutcome:
        """Handle a ``BatchCommitWriteStreams`` invocation (PENDING only)."""
        ...


def select_strategy(stream_type: WriteStreamType) -> WriteStrategy:
    """Return the strategy singleton for ``stream_type``."""
    # Imported locally to break the circular reference between the
    # strategy modules and this base module.
    from bqemulator.streaming.strategies.buffered import BufferedWriteStrategy
    from bqemulator.streaming.strategies.committed import CommittedWriteStrategy
    from bqemulator.streaming.strategies.default import DefaultWriteStrategy
    from bqemulator.streaming.strategies.pending import PendingWriteStrategy

    if stream_type is WriteStreamType.DEFAULT:
        return DefaultWriteStrategy()
    if stream_type is WriteStreamType.COMMITTED:
        return CommittedWriteStrategy()
    if stream_type is WriteStreamType.PENDING:
        return PendingWriteStrategy()
    if stream_type is WriteStreamType.BUFFERED:
        return BufferedWriteStrategy()
    raise ValueError(f"Unsupported stream type: {stream_type}")


__all__ = [
    "AppendOutcome",
    "AppendStatus",
    "CommitOutcome",
    "FlushOutcome",
    "WriteStrategy",
    "select_strategy",
]
