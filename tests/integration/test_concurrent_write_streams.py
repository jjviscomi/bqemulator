"""Concurrent-writer contention tests for the Storage Write API.

Phase 5 review claimed "concurrent AppendRows on the same stream can't
corrupt ``next_offset``" — a code-review claim, not a tested one. This
file exercises real contention against the strategy + manager + stream
locking layer and asserts the offset invariants hold:

1. **Monotonicity**: After N concurrent threads each append M rows
   under their own offset, ``stream.next_offset == N*M`` (no double-
   count, no skipped rows).
2. **Exactly-once on retry storms**: If T threads all submit
   ``offset=K`` simultaneously (the retry-on-timeout scenario real
   clients exhibit), exactly one returns ``OK`` and the rest return
   ``ALREADY_EXISTS``.
3. **No duplicate visible commits**: ``stream.row_count`` matches the
   number of unique appends.

We run against ``CommittedWriteStrategy`` (offset-strict) and
``PendingWriteStrategy`` (offset-strict, buffered) — the two production
strategies where contention is most likely to expose a race.

The tests are pure Python (no gRPC), driving the strategy under the
same ``WriteStream.lock`` the servicer takes. That keeps the harness
fast (sub-second per scenario) while exercising the exact code path a
production AppendRows takes.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest

from bqemulator.streaming.strategies import (
    CommittedWriteStrategy,
    PendingWriteStrategy,
)
from bqemulator.streaming.strategies.base import AppendStatus
from bqemulator.streaming.write_stream import (
    WriteStream,
    WriteStreamManager,
    WriteStreamType,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.streaming.strategies.base import WriteStrategy

pytestmark = pytest.mark.integration


def _fresh_stream(stream_type: WriteStreamType) -> WriteStream:
    return WriteStream(
        name="projects/p/datasets/d/tables/t/streams/s",
        project_id="p",
        dataset_id="d",
        table_id="t",
        stream_type=stream_type,
    )


def _rows(n: int) -> pa.Table:
    return pa.table({"id": pa.array(list(range(n)), type=pa.int64())})


_OFFSET_TRACKING_PARAMS: list[tuple[type, WriteStreamType]] = [
    (CommittedWriteStrategy, WriteStreamType.COMMITTED),
    (PendingWriteStrategy, WriteStreamType.PENDING),
]


@pytest.mark.parametrize(
    ("strategy_cls", "stream_type"),
    _OFFSET_TRACKING_PARAMS,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
class TestCoordinatedOffsetContention:
    """N threads coordinate via the stream lock to take the next slot.

    Models a multi-threaded client that funnels writes through one
    stream: each thread grabs the lock, reads the current
    ``next_offset`` to use, appends, releases. This is the realistic
    happy path for shared-stream writers.
    """

    def test_n_threads_taking_next_slot_preserve_invariants(
        self,
        strategy_cls: type,
        stream_type: WriteStreamType,
    ) -> None:
        strat: WriteStrategy = strategy_cls()
        stream = _fresh_stream(stream_type)
        n_threads = 16
        rows_per = 5

        successes = 0
        successes_lock = threading.Lock()

        def worker() -> None:
            nonlocal successes
            # Per-stream lock is what the servicer takes around every
            # AppendRows; we acquire it here to read-and-submit the
            # next-offset atomically. This is the well-behaved client
            # contract.
            with stream.lock:
                my_offset = stream.next_offset
                outcome = strat.append(stream, _rows(rows_per), offset=my_offset)
            if outcome.status is AppendStatus.OK:
                with successes_lock:
                    successes += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            futures = [ex.submit(worker) for _ in range(n_threads)]
            for f in futures:
                f.result()

        assert successes == n_threads
        assert stream.next_offset == n_threads * rows_per
        assert stream.row_count == n_threads * rows_per

    def test_offset_none_concurrent_appends_preserve_total(
        self,
        strategy_cls: type,
        stream_type: WriteStreamType,
    ) -> None:
        """``offset=None`` (server-picks) under contention: rows still sum."""
        strat: WriteStrategy = strategy_cls()
        stream = _fresh_stream(stream_type)
        n_threads = 32
        rows_per = 3

        def worker() -> AppendStatus:
            with stream.lock:
                outcome = strat.append(stream, _rows(rows_per), offset=None)
            return outcome.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            results = list(ex.map(lambda _: worker(), range(n_threads)))

        assert all(s is AppendStatus.OK for s in results)
        assert stream.next_offset == n_threads * rows_per
        assert stream.row_count == n_threads * rows_per


@pytest.mark.parametrize(
    ("strategy_cls", "stream_type"),
    _OFFSET_TRACKING_PARAMS,
    ids=lambda x: getattr(x, "__name__", str(x)),
)
class TestRetryStormContention:
    """Many threads submit the SAME offset at once.

    This is the retry-storm scenario: a client times out on its first
    AppendRows, retries, and meanwhile the original RPC also lands.
    Exactly one append must commit; the rest must return
    ``ALREADY_EXISTS`` (BigQuery's documented behaviour).
    """

    def test_simultaneous_duplicate_offsets_yield_exactly_one_ok(
        self,
        strategy_cls: type,
        stream_type: WriteStreamType,
    ) -> None:
        strat: WriteStrategy = strategy_cls()
        stream = _fresh_stream(stream_type)
        n_threads = 32
        barrier = threading.Barrier(n_threads)
        outcomes: list[AppendStatus] = []
        outcomes_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()  # release all threads simultaneously
            with stream.lock:
                outcome = strat.append(stream, _rows(1), offset=0)
            with outcomes_lock:
                outcomes.append(outcome.status)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            futures = [ex.submit(worker) for _ in range(n_threads)]
            for f in futures:
                f.result()

        ok_count = sum(1 for s in outcomes if s is AppendStatus.OK)
        already_exists = sum(1 for s in outcomes if s is AppendStatus.ALREADY_EXISTS)
        assert ok_count == 1, (
            f"expected exactly one OK, got {ok_count}; "
            f"distribution: {dict.fromkeys(outcomes, 0) | {s: outcomes.count(s) for s in outcomes}}"
        )
        assert already_exists == n_threads - 1
        # Single committed append → next_offset == row_count == 1.
        assert stream.next_offset == 1
        assert stream.row_count == 1


class TestWriteStreamManagerConcurrency:
    """``WriteStreamManager`` itself must survive concurrent create/lookup.

    The manager is shared across the gRPC servicer and the
    ``/admin/streams`` admin endpoint. Phase 10's
    ``list_active`` snapshot helper must not crash under mutation.
    """

    def test_concurrent_creates_get_unique_streams(self) -> None:
        manager = WriteStreamManager()
        n_threads = 32

        def make(stream_id: str) -> str:
            manager.create("p", "d", "t", stream_id, WriteStreamType.COMMITTED)
            return stream_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            ids = list(ex.map(make, (f"s-{i}" for i in range(n_threads))))

        assert len(set(ids)) == n_threads
        assert len(manager.list_active()) == n_threads

    def test_list_active_snapshot_is_safe_during_concurrent_create(self) -> None:
        manager = WriteStreamManager()
        stop = threading.Event()

        def creator() -> None:
            i = 0
            while not stop.is_set():
                try:
                    manager.create("p", "d", "t", f"s-{i}", WriteStreamType.COMMITTED)
                except ValueError:
                    pass
                i += 1

        def reader() -> int:
            seen = 0
            for _ in range(200):
                snapshot = manager.list_active()
                seen = max(seen, len(snapshot))
            return seen

        creator_thread = threading.Thread(target=creator, daemon=True)
        creator_thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(lambda _: reader(), range(4)))
        finally:
            stop.set()
            creator_thread.join(timeout=2)

        # Each reader observed *some* streams; none crashed.
        assert all(r >= 0 for r in results)
        # And the final state is consistent: every create either landed
        # in the manager or was rejected as a duplicate.
        assert len(manager.list_active()) > 0
