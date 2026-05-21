"""Unit tests for the Avro Storage Read serializer (G3 / ADR 0030).

Pins the BigQuery → Avro type mapping the schema converter implements
and the wire-format contract the row serializer obeys (naked binary
rows; no Avro Object Container File header).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import io
import json

import fastavro
import pyarrow as pa
import pytest

from bqemulator.streaming.avro_serializer import (
    arrow_schema_to_avro_json,
    serialize_arrow_table_to_avro_rows,
)

pytestmark = pytest.mark.unit


class TestArrowSchemaToAvroJson:
    """Pins the BigQuery → Avro type mapping documented in ADR 0030."""

    def test_int64_maps_to_long(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["type"] == "record"
        assert avro["name"] == "Root"
        assert avro["fields"][0]["name"] == "id"
        assert avro["fields"][0]["type"] == "long"

    def test_float64_maps_to_double(self) -> None:
        schema = pa.schema([pa.field("x", pa.float64())])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        # Nullable → union ["null", "double"].
        assert avro["fields"][0]["type"] == ["null", "double"]

    def test_string_maps_to_string(self) -> None:
        schema = pa.schema([pa.field("name", pa.string(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "string"

    def test_bool_maps_to_boolean(self) -> None:
        schema = pa.schema([pa.field("flag", pa.bool_(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "boolean"

    def test_bytes_maps_to_bytes(self) -> None:
        schema = pa.schema([pa.field("blob", pa.binary(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "bytes"

    def test_decimal_maps_to_decimal_logical(self) -> None:
        schema = pa.schema([pa.field("amount", pa.decimal128(38, 9), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        avro_type = avro["fields"][0]["type"]
        assert avro_type["type"] == "bytes"
        assert avro_type["logicalType"] == "decimal"
        assert avro_type["precision"] == 38
        assert avro_type["scale"] == 9

    def test_bignumeric_uses_documented_precision_scale(self) -> None:
        # BIGNUMERIC needs decimal256 — pyarrow decimal128 caps at 38.
        schema = pa.schema([pa.field("big", pa.decimal256(76, 38), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        avro_type = avro["fields"][0]["type"]
        assert avro_type["precision"] == 76
        assert avro_type["scale"] == 38

    def test_date_maps_to_date_logical(self) -> None:
        schema = pa.schema([pa.field("day", pa.date32(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == {"type": "int", "logicalType": "date"}

    def test_timestamp_maps_to_timestamp_micros_logical(self) -> None:
        schema = pa.schema([pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == {
            "type": "long",
            "logicalType": "timestamp-micros",
        }

    def test_time_maps_to_time_micros_logical(self) -> None:
        schema = pa.schema([pa.field("t", pa.time64("us"), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == {"type": "long", "logicalType": "time-micros"}

    def test_array_maps_to_avro_array(self) -> None:
        schema = pa.schema([pa.field("tags", pa.list_(pa.string()), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"]["type"] == "array"
        assert avro["fields"][0]["type"]["items"] == "string"

    def test_struct_maps_to_avro_record(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("x", pa.int64(), nullable=False),
                pa.field("y", pa.string(), nullable=False),
            ]
        )
        schema = pa.schema([pa.field("point", struct_type, nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        record = avro["fields"][0]["type"]
        assert record["type"] == "record"
        assert record["name"] == "point_record"
        assert {f["name"] for f in record["fields"]} == {"x", "y"}

    def test_nullable_wraps_in_null_first_union(self) -> None:
        """Per BQ docs the nullable union puts ``null`` first."""
        schema = pa.schema([pa.field("maybe", pa.int64())])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"][0] == "null"
        assert avro["fields"][0]["type"][1] == "long"
        assert avro["fields"][0]["default"] is None

    def test_non_nullable_emits_bare_type(self) -> None:
        schema = pa.schema([pa.field("required", pa.int64(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "long"
        assert "default" not in avro["fields"][0]

    def test_record_name_override(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema, record_name="MyRoot"))
        assert avro["name"] == "MyRoot"

    def test_unknown_type_falls_back_to_string(self) -> None:
        # GEOGRAPHY surfaces as Arrow string via the storage layer
        # (per ADR 0019). Map type also falls back to string-keyed.
        schema = pa.schema([pa.field("geo", pa.string(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "string"

    def test_output_round_trips_through_fastavro_parse_schema(self) -> None:
        """The emitted JSON must be a structurally valid Avro schema."""
        schema = pa.schema(
            [
                pa.field("id", pa.int64(), nullable=False),
                pa.field("name", pa.string()),
                pa.field("amount", pa.decimal128(38, 9)),
                pa.field("ts", pa.timestamp("us", tz="UTC")),
            ]
        )
        avro_json = arrow_schema_to_avro_json(schema)
        parsed = fastavro.parse_schema(json.loads(avro_json))
        # parse_schema returns a normalised dict; assert the top-level
        # type stayed ``record`` (proves no canonicalisation rejected it).
        assert parsed["type"] == "record"
        assert {f["name"] for f in parsed["fields"]} == {"id", "name", "amount", "ts"}


class TestRequiredFieldOverride:
    """``required_field_names`` overrides the arrow ``nullable`` flag.

    DuckDB's query-result schema marks every column ``nullable=True``
    regardless of the source-table ``mode='REQUIRED'`` flag, so the
    Avro serializer accepts an explicit set of REQUIRED column names
    from the BigQuery catalog and emits a bare ``T`` instead of a
    ``["null", T]`` union for those columns. Without this override the
    Storage Read Avro schema would wrap every column in a union and
    diverge from real BigQuery's canonical shape.
    """

    def test_required_field_emits_bare_type(self) -> None:
        # arrow says nullable, but the catalog says REQUIRED
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),  # nullable=True (default)
                pa.field("name", pa.string()),  # nullable=True (default)
            ]
        )
        avro = json.loads(arrow_schema_to_avro_json(schema, required_field_names=frozenset({"id"})))
        # ``id`` ends up bare ``long`` (REQUIRED override)
        id_field = next(f for f in avro["fields"] if f["name"] == "id")
        assert id_field["type"] == "long"
        assert "default" not in id_field
        # ``name`` stays a union (no REQUIRED override, arrow is nullable)
        name_field = next(f for f in avro["fields"] if f["name"] == "name")
        assert name_field["type"] == ["null", "string"]
        assert name_field["default"] is None

    def test_none_required_keeps_legacy_behavior(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64())])  # nullable=True
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == ["null", "long"]

    def test_empty_required_keeps_legacy_behavior(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64())])
        avro = json.loads(arrow_schema_to_avro_json(schema, required_field_names=frozenset()))
        assert avro["fields"][0]["type"] == ["null", "long"]

    def test_arrow_required_still_wins_without_override(self) -> None:
        # Arrow-side nullable=False alone is sufficient — required_field_names
        # is only needed to override DuckDB's nullable-by-default schemas.
        schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
        avro = json.loads(arrow_schema_to_avro_json(schema))
        assert avro["fields"][0]["type"] == "long"


class TestSerializeArrowTableToAvroRows:
    """Naked binary rows; round-trip through fastavro.schemaless_reader."""

    def test_simple_table_round_trips(self) -> None:
        schema = pa.schema(
            [
                pa.field("id", pa.int64(), nullable=False),
                pa.field("name", pa.string(), nullable=False),
            ]
        )
        table = pa.table(
            {"id": [1, 2, 3], "name": ["a", "b", "c"]},
            schema=schema,
        )
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=3)
        assert decoded == [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b"},
            {"id": 3, "name": "c"},
        ]

    def test_empty_table_returns_empty_bytes(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
        table = pa.table({"id": []}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        assert serialize_arrow_table_to_avro_rows(table, avro_json) == b""

    def test_null_values_encode_via_null_union(self) -> None:
        schema = pa.schema([pa.field("maybe", pa.int64())])  # nullable
        table = pa.table({"maybe": [1, None, 3]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=3)
        assert decoded == [{"maybe": 1}, {"maybe": None}, {"maybe": 3}]

    def test_decimal_round_trips(self) -> None:
        schema = pa.schema([pa.field("amount", pa.decimal128(38, 9), nullable=False)])
        rows = [Decimal("1.234567890"), Decimal("-99.000000000")]
        table = pa.table({"amount": rows}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=2)
        assert decoded[0]["amount"] == Decimal("1.234567890")
        assert decoded[1]["amount"] == Decimal("-99.000000000")

    def test_date_round_trips(self) -> None:
        schema = pa.schema([pa.field("day", pa.date32(), nullable=False)])
        table = pa.table({"day": [date(2026, 5, 20)]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=1)
        assert decoded[0]["day"] == date(2026, 5, 20)

    def test_timestamp_round_trips(self) -> None:
        schema = pa.schema([pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False)])
        sample = datetime(2026, 5, 20, 12, 34, 56, tzinfo=UTC)
        table = pa.table({"ts": [sample]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=1)
        assert decoded[0]["ts"] == sample

    def test_array_round_trips(self) -> None:
        schema = pa.schema([pa.field("tags", pa.list_(pa.string()), nullable=False)])
        table = pa.table({"tags": [["a", "b"], ["c"]]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=2)
        assert decoded == [{"tags": ["a", "b"]}, {"tags": ["c"]}]

    def test_struct_round_trips(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("x", pa.int64(), nullable=False),
                pa.field("y", pa.string(), nullable=False),
            ]
        )
        schema = pa.schema([pa.field("point", struct_type, nullable=False)])
        table = pa.table(
            {"point": [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]},
            schema=schema,
        )
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        decoded = self._decode_rows(binary_rows, avro_json, expected_count=2)
        assert decoded == [{"point": {"x": 1, "y": "a"}}, {"point": {"x": 2, "y": "b"}}]

    def test_bytes_are_emitted_naked_not_in_ocf(self) -> None:
        """Output MUST NOT start with the Avro OCF magic ``Obj\\x01``.

        The Storage Read API carries naked binary rows; an OCF header
        in each chunk would break every official client.
        """
        schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
        table = pa.table({"id": [1]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        binary_rows = serialize_arrow_table_to_avro_rows(table, avro_json)
        assert not binary_rows.startswith(b"Obj\x01"), (
            "Storage Read naked-rows MUST NOT carry the Avro OCF header"
        )

    def test_multiple_rows_concatenate(self) -> None:
        """Single-call output must equal the concatenation of per-row writes."""
        schema = pa.schema([pa.field("v", pa.int64(), nullable=False)])
        table = pa.table({"v": [1, 2, 3, 4, 5]}, schema=schema)
        avro_json = arrow_schema_to_avro_json(schema)
        combined = serialize_arrow_table_to_avro_rows(table, avro_json)
        # Re-encode per-row and concatenate manually.
        parsed = fastavro.parse_schema(json.loads(avro_json))
        per_row = b""
        for v in [1, 2, 3, 4, 5]:
            sink = io.BytesIO()
            fastavro.schemaless_writer(sink, parsed, {"v": v})
            per_row += sink.getvalue()
        assert combined == per_row

    @staticmethod
    def _decode_rows(
        binary_rows: bytes,
        avro_json: str,
        *,
        expected_count: int,
    ) -> list[dict]:
        """Decode naked binary rows back into Python dicts via fastavro."""
        parsed = fastavro.parse_schema(json.loads(avro_json))
        reader = io.BytesIO(binary_rows)
        return [fastavro.schemaless_reader(reader, parsed) for _ in range(expected_count)]
