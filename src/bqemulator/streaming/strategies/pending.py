"""PENDING write-stream strategy.

Rows are buffered in memory until the stream is both

1. Finalized (:py:meth:`FinalizeWriteStream`), AND
2. Included in :py:meth:`BatchCommitWriteStreams`.

Only then are the buffered rows flushed to the target table. This
models real BigQuery's transactional write semantics where multiple
PENDING streams can be batched and atomically committed together.

Offsets follow the same strict monotonic discipline as COMMITTED.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.streaming.strategies.base import (
    AppendOutcome,
    AppendStatus,
    CommitOutcome,
    FlushOutcome,
)
from bqemulator.streaming.write_stream import WriteStreamState, WriteStreamType

if TYPE_CHECKING:
    import pyarrow as pa

    from bqemulator.streaming.write_stream import WriteStream


def _concat(buffer: list[pa.Table]) -> pa.Table | None:
    """Concatenate every buffered table into a single table (or ``None`` if empty)."""
    if not buffer:
        return None
    if len(buffer) == 1:
        return buffer[0]
    import pyarrow as pa  # local import keeps strategy base framework-free

    return pa.concat_tables(buffer)


class PendingWriteStrategy:
    """Buffer rows until BatchCommitWriteStreams."""

    stream_type = WriteStreamType.PENDING

    def append(
        self,
        stream: WriteStream,
        rows: pa.Table,
        offset: int | None,
        *,
        max_buffered_rows: int | None = None,
    ) -> AppendOutcome:
        """Buffer rows against the stream; enforce offset discipline."""
        if stream.state is WriteStreamState.COMMITTED:
            return AppendOutcome(
                status=AppendStatus.STREAM_FINALIZED,
                detail="Stream is already committed",
            )
        if stream.state is WriteStreamState.FINALIZED:
            return AppendOutcome(
                status=AppendStatus.STREAM_FINALIZED,
                detail="Stream is finalized; no further appends allowed",
            )

        expected = stream.next_offset
        if offset is not None:
            if offset < expected:
                return AppendOutcome(
                    status=AppendStatus.ALREADY_EXISTS,
                    offset=offset,
                    detail=(f"Offset {offset} already buffered; next expected {expected}"),
                )
            if offset > expected:
                return AppendOutcome(
                    status=AppendStatus.OUT_OF_RANGE,
                    offset=offset,
                    detail=(f"Offset {offset} beyond next expected {expected}"),
                )

        appended = rows.num_rows
        if max_buffered_rows is not None and stream.row_count + appended > max_buffered_rows:
            return AppendOutcome(
                status=AppendStatus.RESOURCE_EXHAUSTED,
                detail=(
                    f"PENDING stream buffer would exceed the per-stream cap "
                    f"({max_buffered_rows} rows). Finalize + BatchCommit to release."
                ),
            )

        committed_offset = expected
        stream.buffer.append(rows)
        stream.next_offset += appended
        stream.row_count += appended
        # committed_rows is None because nothing is visible until commit.
        return AppendOutcome(
            status=AppendStatus.OK,
            committed_rows=None,
            offset=committed_offset,
        )

    def flush(self, stream: WriteStream, offset: int) -> FlushOutcome:  # noqa: ARG002
        """FlushRows is not valid on PENDING streams."""
        return FlushOutcome(
            ok=False,
            detail="FlushRows not supported on PENDING stream",
        )

    def commit(self, stream: WriteStream) -> CommitOutcome:
        """Flush the entire buffer to the target. Requires FINALIZED state."""
        if stream.state is not WriteStreamState.FINALIZED:
            return CommitOutcome(
                ok=False,
                detail=(
                    "Stream must be finalized before BatchCommit; "
                    f"current state: {stream.state.value}"
                ),
            )
        rows = _concat(stream.buffer)
        stream.buffer.clear()
        stream.state = WriteStreamState.COMMITTED
        return CommitOutcome(ok=True, committed_rows=rows)


__all__ = ["PendingWriteStrategy"]
