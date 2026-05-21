"""Unit tests for :mod:`bqemulator.streaming.write_stream`."""

from __future__ import annotations

import pytest

from bqemulator.streaming.write_stream import (
    DEFAULT_STREAM_SUFFIX,
    WriteStream,
    WriteStreamManager,
    WriteStreamState,
    WriteStreamType,
    build_stream_name,
    default_stream_name,
    parse_stream_name,
    parse_table_parent,
)

pytestmark = pytest.mark.unit


class TestBuildStreamName:
    def test_builds_canonical_name(self) -> None:
        """Names follow BigQuery's ``projects/.../streams/...`` path shape."""
        assert build_stream_name("p", "d", "t", "s1") == "projects/p/datasets/d/tables/t/streams/s1"

    def test_default_stream_name_uses_suffix(self) -> None:
        """The implicit default stream has a reserved suffix."""
        name = default_stream_name("p", "d", "t")
        assert name.endswith(f"/streams/{DEFAULT_STREAM_SUFFIX}")


class TestParseStreamName:
    def test_parses_valid_name(self) -> None:
        """Round-trip: parse produces the project/dataset/table/id quadruple."""
        p, d, t, s = parse_stream_name("projects/p/datasets/d/tables/t/streams/s1")
        assert (p, d, t, s) == ("p", "d", "t", "s1")

    @pytest.mark.parametrize(
        "bad",
        [
            "projects/p/datasets/d/tables/t",  # missing streams segment
            "projects/p/datasets/d/streams/s",  # wrong layout
            "wrong/p/datasets/d/tables/t/streams/s",  # wrong prefix
        ],
    )
    def test_rejects_invalid_names(self, bad: str) -> None:
        """Anything that doesn't match the 4-part layout raises ValueError."""
        with pytest.raises(ValueError):
            parse_stream_name(bad)


class TestParseTableParent:
    def test_parses_valid_parent(self) -> None:
        """Round-trip: parse of the CreateWriteStream parent."""
        assert parse_table_parent("projects/p/datasets/d/tables/t") == (
            "p",
            "d",
            "t",
        )

    def test_rejects_invalid_parent(self) -> None:
        """Non-table parents raise ValueError."""
        with pytest.raises(ValueError):
            parse_table_parent("projects/p")


class TestWriteStreamManager:
    def test_create_returns_new_stream(self) -> None:
        """``create`` constructs a stream with the requested identity."""
        mgr = WriteStreamManager()
        s = mgr.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)
        assert s.name == "projects/p/datasets/d/tables/t/streams/s1"
        assert s.stream_type is WriteStreamType.COMMITTED
        assert s.state is WriteStreamState.OPEN

    def test_create_duplicate_raises(self) -> None:
        """Creating a stream with an already-taken id is an error."""
        mgr = WriteStreamManager()
        mgr.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)
        with pytest.raises(ValueError):
            mgr.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)

    def test_get_returns_existing_stream(self) -> None:
        """``get`` finds streams created by ``create``."""
        mgr = WriteStreamManager()
        s = mgr.create("p", "d", "t", "s1", WriteStreamType.PENDING)
        assert mgr.get(s.name) is s

    def test_get_returns_none_for_unknown(self) -> None:
        """Unknown streams return ``None`` (callers convert to NOT_FOUND)."""
        assert WriteStreamManager().get("does-not-exist") is None

    def test_get_or_create_default_is_idempotent(self) -> None:
        """Two calls return the same default-stream instance."""
        mgr = WriteStreamManager()
        a = mgr.get_or_create_default("p", "d", "t")
        b = mgr.get_or_create_default("p", "d", "t")
        assert a is b
        assert a.stream_type is WriteStreamType.DEFAULT

    def test_delete_removes_stream(self) -> None:
        """``delete`` forgets the stream."""
        mgr = WriteStreamManager()
        s = mgr.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)
        mgr.delete(s.name)
        assert mgr.get(s.name) is None

    def test_clear_removes_all(self) -> None:
        """``clear`` wipes the whole registry."""
        mgr = WriteStreamManager()
        mgr.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)
        mgr.create("p", "d", "t", "s2", WriteStreamType.PENDING)
        mgr.clear()
        assert mgr.get("projects/p/datasets/d/tables/t/streams/s1") is None
        assert mgr.get("projects/p/datasets/d/tables/t/streams/s2") is None


class TestWriteStreamDataclass:
    def test_defaults(self) -> None:
        """A fresh stream starts OPEN at offset 0 with empty buffer."""
        s = WriteStream(
            name="n",
            project_id="p",
            dataset_id="d",
            table_id="t",
            stream_type=WriteStreamType.COMMITTED,
        )
        assert s.state is WriteStreamState.OPEN
        assert s.next_offset == 0
        assert s.row_count == 0
        assert s.buffer == []
        assert s.flushed_rows == 0
