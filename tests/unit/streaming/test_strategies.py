"""Unit tests for Storage Write API strategies."""

from __future__ import annotations

import pyarrow as pa
import pytest

from bqemulator.streaming.strategies import (
    BufferedWriteStrategy,
    CommittedWriteStrategy,
    DefaultWriteStrategy,
    PendingWriteStrategy,
    select_strategy,
)
from bqemulator.streaming.strategies.base import AppendStatus
from bqemulator.streaming.write_stream import (
    WriteStream,
    WriteStreamState,
    WriteStreamType,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def rows() -> pa.Table:
    """A 3-row test table used across strategy tests."""
    return pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})


def _stream(stream_type: WriteStreamType) -> WriteStream:
    return WriteStream(
        name=f"projects/p/datasets/d/tables/t/streams/{stream_type.value.lower()}",
        project_id="p",
        dataset_id="d",
        table_id="t",
        stream_type=stream_type,
    )


# ---------------------------------------------------------------------------
# DEFAULT
# ---------------------------------------------------------------------------


class TestDefaultStrategy:
    def test_append_commits_immediately(self, rows: pa.Table) -> None:
        """DEFAULT returns the rows to commit and advances the row counter."""
        strat = DefaultWriteStrategy()
        stream = _stream(WriteStreamType.DEFAULT)
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.OK
        assert outcome.committed_rows is rows
        assert stream.row_count == 3
        assert stream.next_offset == 3

    def test_append_with_offset_is_rejected(self, rows: pa.Table) -> None:
        """DEFAULT streams do not accept offsets."""
        strat = DefaultWriteStrategy()
        stream = _stream(WriteStreamType.DEFAULT)
        outcome = strat.append(stream, rows, offset=0)
        assert outcome.status is AppendStatus.INVALID_ARGUMENT

    def test_flush_not_supported(self) -> None:
        """FlushRows on DEFAULT is an error."""
        strat = DefaultWriteStrategy()
        outcome = strat.flush(_stream(WriteStreamType.DEFAULT), offset=0)
        assert outcome.ok is False

    def test_commit_not_supported(self) -> None:
        """BatchCommit on DEFAULT is an error."""
        strat = DefaultWriteStrategy()
        outcome = strat.commit(_stream(WriteStreamType.DEFAULT))
        assert outcome.ok is False


# ---------------------------------------------------------------------------
# COMMITTED
# ---------------------------------------------------------------------------


class TestCommittedStrategy:
    def test_append_no_offset_commits(self, rows: pa.Table) -> None:
        """Omitting offset is accepted and the stream advances in order."""
        strat = CommittedWriteStrategy()
        stream = _stream(WriteStreamType.COMMITTED)
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.OK
        assert outcome.offset == 0
        assert stream.next_offset == 3

    def test_append_with_matching_offset(self, rows: pa.Table) -> None:
        """Strictly monotonic offsets succeed."""
        strat = CommittedWriteStrategy()
        stream = _stream(WriteStreamType.COMMITTED)
        assert strat.append(stream, rows, offset=0).status is AppendStatus.OK
        assert strat.append(stream, rows, offset=3).status is AppendStatus.OK

    def test_duplicate_offset_is_already_exists(self, rows: pa.Table) -> None:
        """A lower-than-expected offset means the client re-sent a page."""
        strat = CommittedWriteStrategy()
        stream = _stream(WriteStreamType.COMMITTED)
        strat.append(stream, rows, offset=0)  # advance to 3
        outcome = strat.append(stream, rows, offset=0)
        assert outcome.status is AppendStatus.ALREADY_EXISTS

    def test_gap_offset_is_out_of_range(self, rows: pa.Table) -> None:
        """A higher-than-expected offset indicates a gap."""
        strat = CommittedWriteStrategy()
        stream = _stream(WriteStreamType.COMMITTED)
        outcome = strat.append(stream, rows, offset=5)
        assert outcome.status is AppendStatus.OUT_OF_RANGE

    def test_append_on_finalized_stream_rejected(self, rows: pa.Table) -> None:
        """Once finalized, appends are forbidden."""
        strat = CommittedWriteStrategy()
        stream = _stream(WriteStreamType.COMMITTED)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.STREAM_FINALIZED

    def test_flush_not_supported(self) -> None:
        """Flush is not valid on COMMITTED."""
        assert (
            CommittedWriteStrategy()
            .flush(
                _stream(WriteStreamType.COMMITTED),
                offset=0,
            )
            .ok
            is False
        )

    def test_commit_not_supported(self) -> None:
        """BatchCommit is not valid on COMMITTED."""
        assert CommittedWriteStrategy().commit(_stream(WriteStreamType.COMMITTED)).ok is False


# ---------------------------------------------------------------------------
# PENDING
# ---------------------------------------------------------------------------


class TestPendingStrategy:
    def test_append_buffers_without_commit(self, rows: pa.Table) -> None:
        """PENDING keeps rows invisible until BatchCommit."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.OK
        assert outcome.committed_rows is None  # not visible yet
        assert len(stream.buffer) == 1
        assert stream.row_count == 3

    def test_commit_requires_finalization(self, rows: pa.Table) -> None:
        """BatchCommit before Finalize is refused."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        strat.append(stream, rows, offset=None)
        assert strat.commit(stream).ok is False

    def test_commit_after_finalize_flushes_buffer(self, rows: pa.Table) -> None:
        """Finalize + BatchCommit flushes the whole buffer."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        strat.append(stream, rows, offset=0)
        strat.append(stream, rows, offset=3)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.commit(stream)
        assert outcome.ok is True
        assert outcome.committed_rows is not None
        assert outcome.committed_rows.num_rows == 6
        assert stream.buffer == []

    def test_commit_empty_buffer_succeeds(self) -> None:
        """Committing a finalized-but-empty stream is a no-op success."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.commit(stream)
        assert outcome.ok is True
        assert outcome.committed_rows is None

    def test_second_commit_is_rejected(self, rows: pa.Table) -> None:
        """Once COMMITTED, further appends are rejected."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        stream.state = WriteStreamState.COMMITTED
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.STREAM_FINALIZED

    def test_append_duplicate_offset(self, rows: pa.Table) -> None:
        """Offset discipline is enforced on buffered appends too."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        strat.append(stream, rows, offset=0)
        outcome = strat.append(stream, rows, offset=0)
        assert outcome.status is AppendStatus.ALREADY_EXISTS

    def test_append_gap_offset(self, rows: pa.Table) -> None:
        """A gap offset returns OUT_OF_RANGE."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        outcome = strat.append(stream, rows, offset=7)
        assert outcome.status is AppendStatus.OUT_OF_RANGE

    def test_append_after_finalize(self, rows: pa.Table) -> None:
        """Finalized PENDING streams refuse appends until BatchCommit."""
        strat = PendingWriteStrategy()
        stream = _stream(WriteStreamType.PENDING)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.STREAM_FINALIZED

    def test_flush_not_supported(self) -> None:
        """Flush is not valid on PENDING streams."""
        assert (
            PendingWriteStrategy()
            .flush(
                _stream(WriteStreamType.PENDING),
                offset=0,
            )
            .ok
            is False
        )


# ---------------------------------------------------------------------------
# BUFFERED
# ---------------------------------------------------------------------------


class TestBufferedStrategy:
    def test_append_buffers_rows(self, rows: pa.Table) -> None:
        """BUFFERED holds rows until FlushRows."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.OK
        assert outcome.committed_rows is None
        assert stream.next_offset == 3

    def test_flush_publishes_rows_in_range(self, rows: pa.Table) -> None:
        """Flush with offset=N makes rows [0, N) visible."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        outcome = strat.flush(stream, offset=2)
        assert outcome.ok is True
        assert outcome.committed_rows is not None
        assert outcome.committed_rows.num_rows == 2
        assert stream.flushed_rows == 2

    def test_flush_twice_extends_visible_range(self, rows: pa.Table) -> None:
        """Multiple flushes can each advance the visible frontier."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        strat.append(stream, rows, offset=3)
        first = strat.flush(stream, offset=2)
        second = strat.flush(stream, offset=4)
        assert first.committed_rows is not None
        assert first.committed_rows.num_rows == 2
        assert second.committed_rows is not None
        assert second.committed_rows.num_rows == 2
        assert stream.flushed_rows == 4

    def test_flush_before_any_append_fails(self) -> None:
        """Flushing a stream with no buffered rows returns an error."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        outcome = strat.flush(stream, offset=1)
        assert outcome.ok is False

    def test_flush_stale_offset_is_rejected(self, rows: pa.Table) -> None:
        """Flush offsets must be strictly monotonic."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        strat.flush(stream, offset=3)
        outcome = strat.flush(stream, offset=2)  # less than the current flushed offset
        assert outcome.ok is False

    def test_flush_after_finalize_fails(self, rows: pa.Table) -> None:
        """Flushing a finalized stream is forbidden."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.flush(stream, offset=2)
        assert outcome.ok is False

    def test_flush_same_offset_noop_detection(self, rows: pa.Table) -> None:
        """Flushing the same offset twice returns OUT_OF_RANGE (strict >)."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        strat.flush(stream, offset=3)
        outcome = strat.flush(stream, offset=3)
        assert outcome.ok is False

    def test_append_duplicate_offset(self, rows: pa.Table) -> None:
        """Offset dedup applies on BUFFERED just like COMMITTED/PENDING."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        outcome = strat.append(stream, rows, offset=0)
        assert outcome.status is AppendStatus.ALREADY_EXISTS

    def test_append_gap_offset(self, rows: pa.Table) -> None:
        """Gap offsets return OUT_OF_RANGE."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        outcome = strat.append(stream, rows, offset=5)
        assert outcome.status is AppendStatus.OUT_OF_RANGE

    def test_append_on_finalized_stream_rejected(self, rows: pa.Table) -> None:
        """Appends are refused once the stream is finalized."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        stream.state = WriteStreamState.FINALIZED
        outcome = strat.append(stream, rows, offset=None)
        assert outcome.status is AppendStatus.STREAM_FINALIZED

    def test_flush_beyond_buffered_is_rejected(self, rows: pa.Table) -> None:
        """Cannot flush past the current buffer frontier."""
        strat = BufferedWriteStrategy()
        stream = _stream(WriteStreamType.BUFFERED)
        strat.append(stream, rows, offset=0)
        outcome = strat.flush(stream, offset=10)
        assert outcome.ok is False

    def test_commit_not_supported(self) -> None:
        """BatchCommit is not valid on BUFFERED."""
        assert BufferedWriteStrategy().commit(_stream(WriteStreamType.BUFFERED)).ok is False


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


class TestSelectStrategy:
    @pytest.mark.parametrize(
        ("stream_type", "expected_class"),
        [
            (WriteStreamType.DEFAULT, DefaultWriteStrategy),
            (WriteStreamType.COMMITTED, CommittedWriteStrategy),
            (WriteStreamType.PENDING, PendingWriteStrategy),
            (WriteStreamType.BUFFERED, BufferedWriteStrategy),
        ],
    )
    def test_returns_correct_strategy(
        self,
        stream_type: WriteStreamType,
        expected_class: type,
    ) -> None:
        """Every stream type resolves to its matching strategy class."""
        assert isinstance(select_strategy(stream_type), expected_class)
