"""DEFAULT write-stream strategy.

The DEFAULT stream is implicit per table. Every AppendRows is committed
immediately to the target; offsets are *not* tracked for dedup (the
client may send offsets anyway, but they're advisory — real BigQuery
rejects any offset on DEFAULT as ``INVALID_ARGUMENT``).

Finalize is a no-op for DEFAULT streams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.streaming.strategies.base import (
    AppendOutcome,
    AppendStatus,
    CommitOutcome,
    FlushOutcome,
)
from bqemulator.streaming.write_stream import WriteStreamType

if TYPE_CHECKING:
    import pyarrow as pa

    from bqemulator.streaming.write_stream import WriteStream


class DefaultWriteStrategy:
    """Immediately commit; reject offsets."""

    stream_type = WriteStreamType.DEFAULT

    def append(
        self,
        stream: WriteStream,
        rows: pa.Table,
        offset: int | None,
        *,
        max_buffered_rows: int | None = None,  # noqa: ARG002 — DEFAULT never buffers
    ) -> AppendOutcome:
        """Commit ``rows`` immediately, reject any offset."""
        if offset is not None:
            return AppendOutcome(
                status=AppendStatus.INVALID_ARGUMENT,
                detail="DEFAULT stream does not accept offsets",
            )
        appended = rows.num_rows
        stream.row_count += appended
        # offset echoed back is the next-offset boundary even though it's
        # meaningless for DEFAULT; matches BigQuery client observations.
        echo_offset = stream.next_offset
        stream.next_offset += appended
        return AppendOutcome(
            status=AppendStatus.OK,
            committed_rows=rows,
            offset=echo_offset,
        )

    def flush(self, stream: WriteStream, offset: int) -> FlushOutcome:  # noqa: ARG002
        """FlushRows is not valid on DEFAULT streams."""
        return FlushOutcome(ok=False, detail="FlushRows not supported on DEFAULT stream")

    def commit(self, stream: WriteStream) -> CommitOutcome:  # noqa: ARG002
        """BatchCommit is not valid on DEFAULT streams."""
        return CommitOutcome(
            ok=False,
            detail="BatchCommitWriteStreams not supported on DEFAULT stream",
        )


__all__ = ["DefaultWriteStrategy"]
