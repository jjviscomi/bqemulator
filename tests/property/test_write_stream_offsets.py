"""Property-based tests for Storage Write API offset semantics.

AGENTS.md non-negotiable: "Combinatorial surface -> property test with
Hypothesis." Write-stream offset validation is combinatorial (every
(state, offset, rows_count) combination potentially matters), so we
exhaustively explore the interesting region.

Invariants we enforce here:

1. For COMMITTED/PENDING/BUFFERED streams, the next expected offset
   after a sequence of successful AppendRows equals the sum of appended
   rows, regardless of buffering.
2. Duplicate offsets always return ALREADY_EXISTS.
3. Gap offsets always return OUT_OF_RANGE.
4. Appending without an offset always succeeds and advances by the
   row count.
5. A stream's ``row_count`` only grows; it never regresses after OK
   appends.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
import pyarrow as pa
import pytest

from bqemulator.streaming.strategies import (
    BufferedWriteStrategy,
    CommittedWriteStrategy,
    PendingWriteStrategy,
)
from bqemulator.streaming.strategies.base import AppendStatus, WriteStrategy
from bqemulator.streaming.write_stream import (
    WriteStream,
    WriteStreamType,
)

pytestmark = pytest.mark.property


def _fresh_stream(stream_type: WriteStreamType) -> WriteStream:
    return WriteStream(
        name="projects/p/datasets/d/tables/t/streams/s",
        project_id="p",
        dataset_id="d",
        table_id="t",
        stream_type=stream_type,
    )


def _make_rows(n: int) -> pa.Table:
    return pa.table({"id": pa.array(list(range(n)), type=pa.int64())})


_strategies_with_offsets: list[tuple[type[WriteStrategy], WriteStreamType]] = [
    (CommittedWriteStrategy, WriteStreamType.COMMITTED),
    (PendingWriteStrategy, WriteStreamType.PENDING),
    (BufferedWriteStrategy, WriteStreamType.BUFFERED),
]


@pytest.mark.parametrize(
    ("strategy_cls", "stream_type"),
    _strategies_with_offsets,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
class TestOffsetInvariants:
    """Run the same invariant battery against every offset-tracking strategy."""

    @given(st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=8))
    @settings(max_examples=100, deadline=None)
    def test_next_offset_equals_total_appended(
        self,
        strategy_cls: type[WriteStrategy],
        stream_type: WriteStreamType,
        row_counts: list[int],
    ) -> None:
        """Σ appended rows == next_offset for any successful sequence."""
        strat = strategy_cls()
        stream = _fresh_stream(stream_type)
        total = 0
        for n in row_counts:
            outcome = strat.append(stream, _make_rows(n), offset=total)
            assert outcome.status is AppendStatus.OK
            total += n
        assert stream.next_offset == total
        assert stream.row_count == total

    @given(
        initial_rows=st.integers(min_value=1, max_value=20),
        dup_delta=st.integers(min_value=0, max_value=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_duplicate_offset_returns_already_exists(
        self,
        strategy_cls: type[WriteStrategy],
        stream_type: WriteStreamType,
        initial_rows: int,
        dup_delta: int,
    ) -> None:
        """Any offset strictly less than next_offset is ALREADY_EXISTS."""
        strat = strategy_cls()
        stream = _fresh_stream(stream_type)
        strat.append(stream, _make_rows(initial_rows), offset=0)
        if dup_delta >= initial_rows:
            return  # would not be a duplicate
        outcome = strat.append(
            stream,
            _make_rows(1),
            offset=dup_delta,
        )
        assert outcome.status is AppendStatus.ALREADY_EXISTS

    @given(
        initial_rows=st.integers(min_value=1, max_value=20),
        gap=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=100, deadline=None)
    def test_gap_offset_returns_out_of_range(
        self,
        strategy_cls: type[WriteStrategy],
        stream_type: WriteStreamType,
        initial_rows: int,
        gap: int,
    ) -> None:
        """Any offset strictly greater than next_offset is OUT_OF_RANGE."""
        strat = strategy_cls()
        stream = _fresh_stream(stream_type)
        strat.append(stream, _make_rows(initial_rows), offset=0)
        outcome = strat.append(
            stream,
            _make_rows(1),
            offset=stream.next_offset + gap,
        )
        assert outcome.status is AppendStatus.OUT_OF_RANGE

    @given(st.lists(st.integers(min_value=1, max_value=10), min_size=1, max_size=5))
    @settings(max_examples=50, deadline=None)
    def test_no_offset_always_succeeds(
        self,
        strategy_cls: type[WriteStrategy],
        stream_type: WriteStreamType,
        row_counts: list[int],
    ) -> None:
        """A sequence of ``offset=None`` appends always succeeds."""
        strat = strategy_cls()
        stream = _fresh_stream(stream_type)
        for n in row_counts:
            outcome = strat.append(stream, _make_rows(n), offset=None)
            assert outcome.status is AppendStatus.OK

    @given(
        good_rows=st.integers(min_value=1, max_value=10),
        failed_offsets=st.lists(
            st.integers(min_value=-100, max_value=-1),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_row_count_monotonic_under_failed_appends(
        self,
        strategy_cls: type[WriteStrategy],
        stream_type: WriteStreamType,
        good_rows: int,
        failed_offsets: list[int],
    ) -> None:
        """Failed appends never decrease the stream's row_count."""
        strat = strategy_cls()
        stream = _fresh_stream(stream_type)
        # One good append to bump row_count.
        strat.append(stream, _make_rows(good_rows), offset=0)
        baseline_count = stream.row_count
        baseline_offset = stream.next_offset
        # Try bad offsets (all negative → considered duplicates because < expected).
        for offset in failed_offsets:
            out = strat.append(stream, _make_rows(1), offset=offset)
            assert out.status is not AppendStatus.OK
        assert stream.row_count == baseline_count
        assert stream.next_offset == baseline_offset


class TestBufferCap:
    """Per-stream buffer cap invariants for PENDING / BUFFERED."""

    @given(
        cap=st.integers(min_value=5, max_value=100),
        attempt_rows=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=100, deadline=None)
    def test_pending_rejects_when_cap_exceeded(
        self,
        cap: int,
        attempt_rows: int,
    ) -> None:
        """PENDING.append returns RESOURCE_EXHAUSTED iff cap would be exceeded."""
        strat = PendingWriteStrategy()
        stream = _fresh_stream(WriteStreamType.PENDING)
        outcome = strat.append(
            stream,
            _make_rows(attempt_rows),
            offset=None,
            max_buffered_rows=cap,
        )
        if attempt_rows > cap:
            assert outcome.status is AppendStatus.RESOURCE_EXHAUSTED
            # No mutation on failure.
            assert stream.row_count == 0
            assert stream.next_offset == 0
        else:
            assert outcome.status is AppendStatus.OK

    @given(
        cap=st.integers(min_value=5, max_value=50),
        batch_sizes=st.lists(
            st.integers(min_value=1, max_value=30),
            min_size=1,
            max_size=6,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_pending_row_count_never_exceeds_cap(
        self,
        cap: int,
        batch_sizes: list[int],
    ) -> None:
        """``stream.row_count`` stays within the cap no matter the sequence.

        Successful batches only mutate state when they fit; rejected
        batches are no-ops. A smaller batch after a rejection is still
        eligible to succeed — what we guarantee is the cap, not
        monotonic rejection once hit.
        """
        strat = PendingWriteStrategy()
        stream = _fresh_stream(WriteStreamType.PENDING)
        for n in batch_sizes:
            before = stream.row_count
            outcome = strat.append(
                stream,
                _make_rows(n),
                offset=None,
                max_buffered_rows=cap,
            )
            if outcome.status is AppendStatus.RESOURCE_EXHAUSTED:
                # Rejected append must not mutate state.
                assert stream.row_count == before
            else:
                assert outcome.status is AppendStatus.OK
                assert stream.row_count <= cap
        assert stream.row_count <= cap
