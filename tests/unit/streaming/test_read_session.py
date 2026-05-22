"""Unit tests for the read session manager."""

from __future__ import annotations

import pyarrow as pa
import pytest

from bqemulator.streaming.read_session import (
    create_read_session,
    get_session,
    get_stream_data,
    serialize_arrow_record_batch,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sample_table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "name": pa.array(["a", "b", "c", "d", "e"], type=pa.string()),
        }
    )


class TestCreateReadSession:
    def test_creates_session_with_streams(self, sample_table: pa.Table) -> None:
        # Real BigQuery caps the stream count at 1 for tables under
        # ~1 MB regardless of ``max_stream_count``; the emulator
        # matches that contract (P3.d). The 5-row test table is well
        # under the threshold so a ``max_streams=2`` request yields 1
        # stream.
        state = create_read_session("proj", "table_ref", sample_table, max_streams=2)
        assert state.session_name.startswith("projects/proj/")
        assert len(state.streams) == 1
        assert state.table.num_rows == 5

    def test_single_stream(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table, max_streams=1)
        assert len(state.streams) == 1
        assert state.streams[0].start_row == 0
        assert state.streams[0].end_row == 5

    def test_max_streams_capped_at_10(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table, max_streams=100)
        assert len(state.streams) <= 10

    def test_empty_table(self) -> None:
        empty = pa.table({"x": pa.array([], type=pa.int64())})
        state = create_read_session("proj", "ref", empty, max_streams=3)
        assert state.table.num_rows == 0

    def test_selected_fields_projection(self, sample_table: pa.Table) -> None:
        state = create_read_session(
            "proj",
            "ref",
            sample_table,
            selected_fields=["name"],
        )
        assert state.table.column_names == ["name"]
        assert state.table.num_rows == 5

    def test_session_stored_for_lookup(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table)
        retrieved = get_session(state.session_name)
        assert retrieved is state


class TestGetStreamData:
    def test_returns_slice_for_valid_stream(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table, max_streams=2)
        stream_name = state.streams[0].name
        data = get_stream_data(state.session_name, stream_name)
        assert data is not None
        assert data.num_rows > 0

    def test_returns_none_for_unknown_session(self) -> None:
        assert get_stream_data("ghost_session", "ghost_stream") is None

    def test_returns_none_for_unknown_stream(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table)
        assert get_stream_data(state.session_name, "ghost_stream") is None

    def test_all_streams_cover_all_rows(self, sample_table: pa.Table) -> None:
        state = create_read_session("proj", "ref", sample_table, max_streams=3)
        total_rows = 0
        for stream in state.streams:
            data = get_stream_data(state.session_name, stream.name)
            assert data is not None
            total_rows += data.num_rows
        assert total_rows == sample_table.num_rows


class TestSerializeArrowRecordBatch:
    """Pin the bare-message contract (issue #15).

    ``serialize_arrow_record_batch`` MUST emit a single IPC
    record-batch message (no schema-message prefix, no EOS-marker
    suffix). ``pa.ipc.read_record_batch(bytes, schema)`` is the
    consumer path real Storage Read clients use; if the function
    regresses to emitting a full stream, this test fails because
    ``read_record_batch`` raises ``OSError: Expected IPC message of
    type record batch but got schema``.
    """

    def test_round_trips_as_bare_batch_message(self, sample_table: pa.Table) -> None:
        batch = sample_table.combine_chunks().to_batches()[0]
        msg_bytes = serialize_arrow_record_batch(batch)
        assert isinstance(msg_bytes, bytes)
        assert len(msg_bytes) > 0

        # Deserialize via the documented consumer path — ``read_record_batch``
        # requires a known schema and refuses any prefix message.
        result = pa.ipc.read_record_batch(msg_bytes, batch.schema)
        assert result.num_rows == batch.num_rows
        assert result.schema == batch.schema

    def test_rejects_full_stream_consumer_pattern(self, sample_table: pa.Table) -> None:
        # Belt-and-braces: ``open_stream`` would still parse a full
        # IPC stream silently. We assert the output is NOT a stream
        # by checking it lacks the schema-message prefix that
        # ``open_stream`` requires.
        batch = sample_table.combine_chunks().to_batches()[0]
        msg_bytes = serialize_arrow_record_batch(batch)
        with pytest.raises((OSError, pa.lib.ArrowInvalid)):
            pa.ipc.open_stream(msg_bytes).read_all()
