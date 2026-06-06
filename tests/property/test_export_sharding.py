"""Property-based tests for EXPORT DATA shard distribution (RFC 0001 / ADR 0043).

The row-range splitter that backs size-based wildcard sharding must,
for any row count and shard count, partition the rows into contiguous,
balanced ranges that cover every row exactly once — i.e. no row is
dropped or duplicated across shards, regardless of how the size proxy
chooses the shard count.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.jobs.executor import _shard_offsets

pytestmark = pytest.mark.property


@given(
    num_rows=st.integers(min_value=0, max_value=10_000),
    shard_count=st.integers(min_value=1, max_value=64),
)
def test_shard_offsets_partition_invariants(num_rows: int, shard_count: int) -> None:
    """Offsets are contiguous from 0, cover all rows exactly once, and stay balanced."""
    offsets = _shard_offsets(num_rows, shard_count)

    assert len(offsets) == shard_count

    position = 0
    lengths: list[int] = []
    for offset, length in offsets:
        assert offset == position, "ranges must be contiguous with no gaps or overlap"
        assert length >= 0
        position += length
        lengths.append(length)

    # Every row is covered exactly once (sum of shard rows == SELECT rows).
    assert position == num_rows
    # Rows are spread as evenly as possible across shards.
    assert max(lengths) - min(lengths) <= 1
