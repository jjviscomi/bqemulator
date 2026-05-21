"""Unit tests for :mod:`bqemulator.streaming.arrow_deserializer`."""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.ipc
import pytest

from bqemulator.streaming.arrow_deserializer import deserialize_arrow_rows

pytestmark = pytest.mark.unit


def _split_arrow_ipc(table: pa.Table) -> tuple[bytes, bytes]:
    """Split an Arrow IPC stream into (schema_bytes, batch_bytes)."""
    sink = io.BytesIO()
    writer = pa.ipc.new_stream(sink, table.schema)
    for batch in table.to_batches():
        writer.write_batch(batch)
    writer.close()
    full = sink.getvalue()

    # Recompose: schema-only stream.
    schema_sink = io.BytesIO()
    writer = pa.ipc.new_stream(schema_sink, table.schema)
    writer.close()
    schema_bytes = schema_sink.getvalue()

    # The full stream is: {schema header}{batches}{EOS marker}. The schema-only
    # stream has the same header followed by the EOS marker. Strip the EOS
    # from ``schema_bytes`` to get just the header, then slice ``full`` at the
    # same boundary to split off the batches.
    schema_header_len = len(schema_bytes) - 8  # 8-byte EOS sentinel
    return full[:schema_header_len], full[schema_header_len:]


class TestDeserializeArrowRows:
    def test_round_trips_simple_table(self) -> None:
        """Schema + record batch bytes round-trip back to the original table."""
        table = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "name": pa.array(["a", "b", "c"], type=pa.string()),
            }
        )
        schema_bytes, batch_bytes = _split_arrow_ipc(table)
        result = deserialize_arrow_rows(schema_bytes, batch_bytes)
        assert result.column_names == ["id", "name"]
        assert result.num_rows == 3
        assert result.column("id").to_pylist() == [1, 2, 3]

    def test_empty_batch_returns_schema_only_table(self) -> None:
        """An empty record batch yields a zero-row table with the schema."""
        schema = pa.schema([pa.field("x", pa.int64())])
        schema_sink = io.BytesIO()
        writer = pa.ipc.new_stream(schema_sink, schema)
        writer.close()
        schema_bytes = schema_sink.getvalue()

        result = deserialize_arrow_rows(schema_bytes, b"")
        assert result.num_rows == 0
        assert result.column_names == ["x"]

    def test_empty_schema_raises(self) -> None:
        """Empty schema bytes are a programmer error."""
        with pytest.raises(ValueError):
            deserialize_arrow_rows(b"", b"anything")

    def test_malformed_schema_raises(self) -> None:
        """Corrupt schema bytes produce a readable ValueError."""
        with pytest.raises(ValueError):
            deserialize_arrow_rows(b"totally garbage bytes here", b"more garbage")
