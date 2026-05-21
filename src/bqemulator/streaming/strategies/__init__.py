"""Write-stream strategies for the Storage Write API.

Each :class:`WriteStreamType` has a corresponding strategy that defines:

* ``append`` — where the rows go (committed immediately vs. buffered).
* ``flush`` — semantics of :py:meth:`FlushRows` (BUFFERED only).
* ``commit`` — semantics of :py:meth:`BatchCommitWriteStreams` (PENDING only).

The strategy protocol is framework-free so it can be unit-tested without
DuckDB or gRPC. The caller injects a ``commit_rows`` callable that
performs the actual database insert.
"""

from __future__ import annotations

from bqemulator.streaming.strategies.base import (
    AppendOutcome,
    CommitOutcome,
    FlushOutcome,
    WriteStrategy,
    select_strategy,
)
from bqemulator.streaming.strategies.buffered import BufferedWriteStrategy
from bqemulator.streaming.strategies.committed import CommittedWriteStrategy
from bqemulator.streaming.strategies.default import DefaultWriteStrategy
from bqemulator.streaming.strategies.pending import PendingWriteStrategy

__all__ = [
    "AppendOutcome",
    "BufferedWriteStrategy",
    "CommitOutcome",
    "CommittedWriteStrategy",
    "DefaultWriteStrategy",
    "FlushOutcome",
    "PendingWriteStrategy",
    "WriteStrategy",
    "select_strategy",
]
