"""Unit tests for the read session manager."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
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


# Hypothesis strategies for the property test below. The repo
# convention (cited by CodeRabbit on PR #31) is to use Hypothesis
# for combinatorial surfaces — type × nullability × row-count is a
# textbook fit. We restrict to scalar types Arrow can round-trip
# without DuckDB-side coercion, since this test exercises the
# pyarrow IPC layer in isolation (not the full read-session path).

_HY_TYPES = st.sampled_from(
    [
        pa.int8(),
        pa.int16(),
        pa.int32(),
        pa.int64(),
        pa.uint8(),
        pa.uint16(),
        pa.uint32(),
        pa.uint64(),
        pa.float32(),
        pa.float64(),
        pa.bool_(),
        pa.string(),
        pa.binary(),
        pa.date32(),
        pa.timestamp("us"),
    ],
)


@st.composite
def _hy_record_batch(draw: st.DrawFn) -> pa.RecordBatch:
    """Generate a ``pa.RecordBatch`` with random schema and row count."""
    n_cols = draw(st.integers(min_value=1, max_value=4))
    n_rows = draw(st.integers(min_value=0, max_value=12))
    fields: list[pa.Field] = []
    arrays: list[pa.Array] = []
    for i in range(n_cols):
        arrow_type = draw(_HY_TYPES)
        nullable = draw(st.booleans())
        fields.append(pa.field(f"c{i}", arrow_type, nullable=nullable))
        # Build values via a type-driven helper. Each ``pa.array(...)``
        # call validates the values against the declared type, so we
        # only need to draw shape-compatible primitives.
        if pa.types.is_integer(arrow_type) or pa.types.is_unsigned_integer(arrow_type):
            vals: list = draw(
                st.lists(st.integers(min_value=0, max_value=100), min_size=n_rows, max_size=n_rows)
            )
        elif pa.types.is_floating(arrow_type):
            vals = draw(
                st.lists(
                    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
                    min_size=n_rows,
                    max_size=n_rows,
                )
            )
        elif pa.types.is_boolean(arrow_type):
            vals = draw(st.lists(st.booleans(), min_size=n_rows, max_size=n_rows))
        elif pa.types.is_string(arrow_type):
            vals = draw(
                st.lists(st.text(min_size=0, max_size=16), min_size=n_rows, max_size=n_rows)
            )
        elif pa.types.is_binary(arrow_type):
            vals = draw(
                st.lists(st.binary(min_size=0, max_size=16), min_size=n_rows, max_size=n_rows)
            )
        elif pa.types.is_date(arrow_type) or pa.types.is_timestamp(arrow_type):
            # ``pa.array`` will accept Python ``int`` / ``None`` and
            # interpret as the underlying logical unit.
            vals = draw(
                st.lists(
                    st.integers(min_value=0, max_value=1_000_000),
                    min_size=n_rows,
                    max_size=n_rows,
                )
            )
        else:  # pragma: no cover — strategy is exhaustive
            raise AssertionError(f"unhandled type in strategy: {arrow_type}")
        # Optionally null-out some entries when the field is nullable.
        if nullable and vals:
            mask = draw(st.lists(st.booleans(), min_size=n_rows, max_size=n_rows))
            vals = [None if m else v for v, m in zip(vals, mask, strict=True)]
        arrays.append(pa.array(vals, type=arrow_type))
    return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


class TestSerializeArrowRecordBatchProperties:
    """Property-based round-trip — generates batches with varied
    types, nullability, and row counts (including zero) and asserts
    the bare-message ↔ ``read_record_batch`` round-trip stays
    invariant across the combinatorial surface.
    """

    @given(batch=_hy_record_batch())
    @settings(
        deadline=None,
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_round_trip_property(self, batch: pa.RecordBatch) -> None:
        msg_bytes = serialize_arrow_record_batch(batch)
        result = pa.ipc.read_record_batch(msg_bytes, batch.schema)
        assert result.num_rows == batch.num_rows
        assert result.schema == batch.schema
        # And the bare-message contract still holds — full-stream
        # readers must refuse the payload.
        with pytest.raises((OSError, pa.lib.ArrowInvalid)):
            pa.ipc.open_stream(msg_bytes).read_all()
