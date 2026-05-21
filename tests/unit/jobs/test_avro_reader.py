"""Unit tests for the fastavro-based Avro → Arrow bridge (G1 follow-up).

The bridge fires when DuckDB's native ``read_avro`` can't handle a
particular Avro feature — currently the ``decimal`` logical type.
The integration test in ``tests/integration/test_load_avro_orc.py``
exercises the end-to-end load path; this unit file pins:

* schema-to-Arrow mapping for primitives + logical types + structs +
  nullable unions + arrays + maps + bytes
* ``is_decimal_logical_avro`` pre-check semantics (returns True for
  nested decimals; False for non-decimal schemas; False on read error)
* the optional-extra contract (clear ``UnsupportedFeatureError`` when
  ``fastavro`` is missing — same shape as the ORC ``pyorc`` fallback)
* corrupt / missing files → ``InvalidQueryError`` (NOT 501)
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import mock

import fastavro
import pyarrow as pa
import pytest

from bqemulator.domain.errors import InvalidQueryError, UnsupportedFeatureError
from bqemulator.jobs.avro_reader import (
    _avro_field_to_arrow,
    _schema_has_decimal_logical,
    is_decimal_logical_avro,
    read_avro_to_arrow,
)

pytestmark = pytest.mark.unit


def _write_avro(path: Path, schema: dict, records: list[dict]) -> None:
    parsed = fastavro.parse_schema(schema)
    with path.open("wb") as fh:
        fastavro.writer(fh, parsed, records)


class TestAvroFieldToArrow:
    """Per-shape Avro schema → pyarrow type mapping."""

    @pytest.mark.parametrize(
        ("avro_type", "expected"),
        [
            ("boolean", pa.bool_()),
            ("int", pa.int32()),
            ("long", pa.int64()),
            ("float", pa.float32()),
            ("double", pa.float64()),
            ("bytes", pa.binary()),
            ("string", pa.string()),
        ],
    )
    def test_primitives(self, avro_type: str, expected: pa.DataType) -> None:
        assert _avro_field_to_arrow(avro_type) == expected

    def test_nullable_union_drops_null_branch(self) -> None:
        assert _avro_field_to_arrow(["null", "string"]) == pa.string()
        assert _avro_field_to_arrow(["string", "null"]) == pa.string()

    def test_multi_type_union_falls_back_to_string(self) -> None:
        assert _avro_field_to_arrow(["int", "string"]) == pa.string()

    def test_decimal_logical_type_preserves_precision_scale(self) -> None:
        result = _avro_field_to_arrow(
            {"type": "bytes", "logicalType": "decimal", "precision": 18, "scale": 4},
        )
        assert pa.types.is_decimal(result)
        assert result.precision == 18
        assert result.scale == 4

    def test_date_logical_type(self) -> None:
        assert _avro_field_to_arrow({"type": "int", "logicalType": "date"}) == pa.date32()

    def test_timestamp_millis_logical_type(self) -> None:
        result = _avro_field_to_arrow(
            {"type": "long", "logicalType": "timestamp-millis"},
        )
        assert result == pa.timestamp("us", tz="UTC")

    def test_timestamp_micros_logical_type(self) -> None:
        result = _avro_field_to_arrow(
            {"type": "long", "logicalType": "timestamp-micros"},
        )
        assert result == pa.timestamp("us", tz="UTC")

    def test_record_maps_to_struct(self) -> None:
        result = _avro_field_to_arrow(
            {
                "type": "record",
                "name": "Inner",
                "fields": [
                    {"name": "a", "type": "string"},
                    {"name": "b", "type": "long"},
                ],
            },
        )
        assert pa.types.is_struct(result)
        assert result.num_fields == 2
        assert result.field(0).name == "a"
        assert result.field(0).type == pa.string()
        assert result.field(1).type == pa.int64()

    def test_array_maps_to_list(self) -> None:
        result = _avro_field_to_arrow({"type": "array", "items": "int"})
        assert pa.types.is_list(result)
        assert result.value_type == pa.int32()

    def test_map_maps_to_pa_map(self) -> None:
        result = _avro_field_to_arrow({"type": "map", "values": "long"})
        assert pa.types.is_map(result)
        assert result.key_type == pa.string()
        assert result.item_type == pa.int64()

    def test_bytes_via_dict_form(self) -> None:
        assert _avro_field_to_arrow({"type": "bytes"}) == pa.binary()

    def test_unknown_primitive_falls_back_to_string(self) -> None:
        # Defensive fallback — Avro spec doesn't include a ``uuid``
        # primitive at the string level (it's a logical type on
        # string), but unrecognised strings should map cleanly.
        assert _avro_field_to_arrow("uuid") == pa.string()


class TestSchemaHasDecimalLogical:
    """Pre-check used by the executor to route around DuckDB's BLOB cast."""

    def test_top_level_decimal_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {"name": "v", "type": {"type": "bytes", "logicalType": "decimal", "precision": 5}}
            ],
        }
        assert _schema_has_decimal_logical(schema) is True

    def test_nested_record_decimal_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {
                    "name": "inner",
                    "type": {
                        "type": "record",
                        "name": "I",
                        "fields": [
                            {
                                "name": "v",
                                "type": {
                                    "type": "bytes",
                                    "logicalType": "decimal",
                                    "precision": 5,
                                },
                            },
                        ],
                    },
                },
            ],
        }
        assert _schema_has_decimal_logical(schema) is True

    def test_array_of_decimal_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {
                    "name": "xs",
                    "type": {
                        "type": "array",
                        "items": {"type": "bytes", "logicalType": "decimal", "precision": 5},
                    },
                },
            ],
        }
        assert _schema_has_decimal_logical(schema) is True

    def test_map_of_decimal_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {
                    "name": "m",
                    "type": {
                        "type": "map",
                        "values": {"type": "bytes", "logicalType": "decimal", "precision": 5},
                    },
                },
            ],
        }
        assert _schema_has_decimal_logical(schema) is True

    def test_union_with_decimal_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {
                    "name": "maybe_v",
                    "type": [
                        "null",
                        {"type": "bytes", "logicalType": "decimal", "precision": 5},
                    ],
                },
            ],
        }
        assert _schema_has_decimal_logical(schema) is True

    def test_non_decimal_schema_not_detected(self) -> None:
        schema = {
            "type": "record",
            "name": "R",
            "fields": [
                {"name": "id", "type": "long"},
                {"name": "name", "type": ["null", "string"]},
            ],
        }
        assert _schema_has_decimal_logical(schema) is False


class TestIsDecimalLogicalAvro:
    """File-based pre-check — returns False on any read error."""

    def test_detects_decimal_in_real_file(self, tmp_path: Path) -> None:
        path = tmp_path / "decimal.avro"
        _write_avro(
            path,
            {
                "type": "record",
                "name": "R",
                "fields": [
                    {
                        "name": "v",
                        "type": {
                            "type": "bytes",
                            "logicalType": "decimal",
                            "precision": 18,
                            "scale": 4,
                        },
                    },
                ],
            },
            [{"v": Decimal("1.2345")}],
        )
        assert is_decimal_logical_avro(str(path)) is True

    def test_returns_false_for_non_decimal(self, tmp_path: Path) -> None:
        path = tmp_path / "simple.avro"
        _write_avro(
            path,
            {
                "type": "record",
                "name": "R",
                "fields": [{"name": "id", "type": "long"}],
            },
            [{"id": 1}],
        )
        assert is_decimal_logical_avro(str(path)) is False

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        # No exception — the executor's own try/except handles the
        # genuine read failure; the pre-check just routes to the
        # right reader.
        assert is_decimal_logical_avro(str(tmp_path / "missing.avro")) is False

    def test_returns_false_for_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.avro"
        path.write_bytes(b"NOT_AVRO" * 100)
        assert is_decimal_logical_avro(str(path)) is False


class TestReadAvroToArrow:
    """End-to-end fastavro → Arrow round-trip for the decimal logical type."""

    def test_basic_decimal_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "d.avro"
        _write_avro(
            path,
            {
                "type": "record",
                "name": "R",
                "fields": [
                    {"name": "id", "type": "long"},
                    {
                        "name": "v",
                        "type": {
                            "type": "bytes",
                            "logicalType": "decimal",
                            "precision": 18,
                            "scale": 4,
                        },
                    },
                ],
            },
            [
                {"id": 1, "v": Decimal("123.4500")},
                {"id": 2, "v": Decimal("-0.0001")},
            ],
        )

        table = read_avro_to_arrow(str(path))
        assert table.num_rows == 2
        assert table.column_names == ["id", "v"]
        v_type = table.schema.field("v").type
        assert pa.types.is_decimal(v_type)
        assert v_type.precision == 18
        assert v_type.scale == 4
        rows = table.to_pylist()
        assert rows[0]["v"] == Decimal("123.4500")
        assert rows[1]["v"] == Decimal("-0.0001")

    def test_missing_file_raises_invalid_query(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidQueryError, match="not found"):
            read_avro_to_arrow(str(tmp_path / "missing.avro"))

    def test_corrupt_file_raises_invalid_query(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.avro"
        path.write_bytes(b"NOT_AVRO" * 100)
        with pytest.raises(InvalidQueryError, match="Failed to read"):
            read_avro_to_arrow(str(path))

    def test_unsupported_when_fastavro_missing(self, tmp_path: Path) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "fastavro":
                raise ImportError("simulated: fastavro missing")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(UnsupportedFeatureError, match=r"avro.*extra"),
        ):
            read_avro_to_arrow(str(tmp_path / "anything.avro"))


class TestReadAvroSchemaRequirements:
    """Top-level schema must be a record; non-records raise immediately."""

    def test_non_record_top_level_raises(self, tmp_path: Path) -> None:
        # A primitive-only Avro file: fastavro lets you write a stream
        # of bare longs (no enclosing record). Our bridge expects a
        # record-of-records, since that's the only shape BigQuery
        # exports. We construct a minimal file by hand to bypass
        # ``_write_avro``'s record assumption.
        path = tmp_path / "primitive.avro"
        with path.open("wb") as fh:
            fastavro.writer(fh, fastavro.parse_schema("long"), [1, 2, 3])

        with pytest.raises(InvalidQueryError):
            read_avro_to_arrow(str(path))
