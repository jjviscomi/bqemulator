"""BUFFERED write-stream strategy.

Rows are buffered in memory until :py:meth:`FlushRows` is called with
an offset. Rows *up to and including* that offset become visible in the
target table; everything past that offset remains buffered.

Offsets follow the same strict monotonic discipline as COMMITTED/PENDING.
Multiple flushes on the same stream are legal so long as the flush
offset is monotonic and within the window already appended.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa

from bqemulator.streaming.strategies.base import (
    AppendOutcome,
    AppendStatus,
    CommitOutcome,
    FlushOutcome,
)
from bqemulator.streaming.write_stream import WriteStreamState, WriteStreamType

if TYPE_CHECKING:
    from bqemulator.streaming.write_stream import WriteStream


class BufferedWriteStrategy:
    """Buffer rows until FlushRows."""

    stream_type = WriteStreamType.BUFFERED

    def append(
        self,
        stream: WriteStream,
        rows: pa.Table,
        offset: int | None,
        *,
        max_buffered_rows: int | None = None,
    ) -> AppendOutcome:
        """Buffer rows with strict offset discipline."""
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
                    detail=(f"Offset {offset} already buffered; next expected {expected}"),
                )
            if offset > expected:
                return AppendOutcome(
                    status=AppendStatus.OUT_OF_RANGE,
                    offset=offset,
                    detail=(f"Offset {offset} beyond next expected {expected}"),
                )

        appended = rows.num_rows
        # Only un-flushed rows count against the buffer cap — flushed rows
        # are already in DuckDB.
        pending_rows = stream.next_offset - stream.flushed_rows + appended
        if max_buffered_rows is not None and pending_rows > max_buffered_rows:
            return AppendOutcome(
                status=AppendStatus.RESOURCE_EXHAUSTED,
                detail=(
                    f"BUFFERED stream pending-rows ({pending_rows}) would "
                    f"exceed per-stream cap ({max_buffered_rows}). "
                    "Call FlushRows to release."
                ),
            )

        committed_offset = expected
        stream.buffer.append(rows)
        stream.next_offset += appended
        stream.row_count += appended
        return AppendOutcome(
            status=AppendStatus.OK,
            committed_rows=None,
            offset=committed_offset,
        )

    def flush(self, stream: WriteStream, offset: int) -> FlushOutcome:
        """Publish rows up to and including ``offset`` to the target.

        ``offset`` is the exclusive upper bound in BigQuery's semantics:
        every row at index ``< offset`` becomes visible. ``offset`` must be
        strictly greater than the prior flushed offset and at most
        ``next_offset`` (the count of rows buffered so far).
        """
        if stream.state is not WriteStreamState.OPEN:
            return FlushOutcome(
                ok=False,
                detail=f"Cannot flush stream in {stream.state.value} state",
            )
        # Real BigQuery wording for an out-of-range FlushRows offset is
        # "Offset N is beyond the end of the stream Entity: <stream>"
        # — including the entity name in the message itself so the
        # error surfaces from one log line. The gRPC-corpus conformance
        # suite asserts this contract.
        if offset > stream.next_offset:
            return FlushOutcome(
                ok=False,
                offset=offset,
                detail=(f"Offset {offset} is beyond the end of the stream Entity: {stream.name}"),
            )
        if offset <= stream.flushed_rows:
            return FlushOutcome(
                ok=False,
                offset=offset,
                detail=(
                    f"Offset {offset} is beyond the end of the stream Entity: {stream.name}"
                    if stream.flushed_rows == 0 and stream.next_offset == 0
                    else f"Offset {offset} already flushed; next expected > {stream.flushed_rows}"
                ),
            )

        # Gather rows [flushed_rows, offset) from the buffer. We keep the
        # buffer as a list of row-batches and materialise a slice that
        # covers the requested range.
        rows_to_flush = offset - stream.flushed_rows
        if rows_to_flush == 0:
            return FlushOutcome(ok=True, offset=offset)

        buffered = pa.concat_tables(stream.buffer) if stream.buffer else None
        if buffered is None:
            return FlushOutcome(
                ok=False,
                offset=offset,
                detail="Internal error: buffer empty but offset > flushed_rows",
            )
        # Index into the buffered table relative to flushed_rows. Because
        # we never delete from the buffer, buffer[0] starts at offset=0.
        slice_start = stream.flushed_rows
        flushed_table = buffered.slice(slice_start, rows_to_flush)
        stream.flushed_rows = offset
        return FlushOutcome(ok=True, offset=offset, committed_rows=flushed_table)

    def commit(self, stream: WriteStream) -> CommitOutcome:  # noqa: ARG002
        """BatchCommit is not valid on BUFFERED streams."""
        return CommitOutcome(
            ok=False,
            detail="BatchCommit not supported on BUFFERED stream",
        )


__all__ = ["BufferedWriteStrategy"]
