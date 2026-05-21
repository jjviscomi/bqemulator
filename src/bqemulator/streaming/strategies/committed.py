"""COMMITTED write-stream strategy.

Every AppendRows commits rows immediately to the target table, with
strict offset dedup:

* First AppendRows on a fresh stream must start at offset 0.
* Subsequent offsets must equal the stream's ``next_offset``.
* Lower offset → ALREADY_EXISTS (the prior append already made those
  rows visible).
* Higher offset → OUT_OF_RANGE (gap in the sequence).
* Omitted offset — accepted; appended at the current position (parity
  with the real service's best-effort mode).
* After Finalize, any further AppendRows is rejected.
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


class CommittedWriteStrategy:
    """Immediate commit with offset-based exactly-once semantics."""

    stream_type = WriteStreamType.COMMITTED

    def append(
        self,
        stream: WriteStream,
        rows: pa.Table,
        offset: int | None,
        *,
        max_buffered_rows: int | None = None,  # noqa: ARG002 — COMMITTED never buffers
    ) -> AppendOutcome:
        """Validate offset, commit immediately if in-sequence."""
        if stream.state is not WriteStreamState.OPEN:
            return AppendOutcome(
                status=AppendStatus.STREAM_FINALIZED,
                detail=f"Stream is {stream.state.value}",
            )

        expected = stream.next_offset
        if offset is not None:
            if offset < expected:
                return AppendOutcome(
                    status=AppendStatus.ALREADY_EXISTS,
                    offset=offset,
                    detail=(f"Offset {offset} already committed; next expected {expected}"),
                )
            if offset > expected:
                return AppendOutcome(
                    status=AppendStatus.OUT_OF_RANGE,
                    offset=offset,
                    detail=(f"Offset {offset} beyond next expected {expected}"),
                )

        appended = rows.num_rows
        committed_offset = expected
        stream.next_offset += appended
        stream.row_count += appended
        return AppendOutcome(
            status=AppendStatus.OK,
            committed_rows=rows,
            offset=committed_offset,
        )

    def flush(self, stream: WriteStream, offset: int) -> FlushOutcome:  # noqa: ARG002
        """FlushRows is not valid on COMMITTED streams."""
        return FlushOutcome(
            ok=False,
            detail="FlushRows not supported on COMMITTED stream",
        )

    def commit(self, stream: WriteStream) -> CommitOutcome:  # noqa: ARG002
        """BatchCommit is not valid on COMMITTED streams."""
        return CommitOutcome(
            ok=False,
            detail="BatchCommit not supported on COMMITTED stream",
        )


__all__ = ["CommittedWriteStrategy"]
