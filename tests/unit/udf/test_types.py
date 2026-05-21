"""Unit tests for UDF type mapping."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.udf.types import parse_bq_type_string, python_to_json_coerce, render_duckdb_type

pytestmark = pytest.mark.unit


class TestRenderDuckdbType:
    def test_scalar_mappings(self) -> None:
        assert render_duckdb_type({"typeKind": "INT64"}) == "BIGINT"
        assert render_duckdb_type({"typeKind": "FLOAT64"}) == "DOUBLE"
        assert render_duckdb_type({"typeKind": "STRING"}) == "VARCHAR"
        assert render_duckdb_type({"typeKind": "BOOL"}) == "BOOLEAN"
        assert render_duckdb_type({"typeKind": "BYTES"}) == "BLOB"

    def test_any_type_when_none(self) -> None:
        assert render_duckdb_type(None) == "ANY"

    def test_array_type(self) -> None:
        bq = {"typeKind": "ARRAY", "arrayElementType": {"typeKind": "INT64"}}
        assert render_duckdb_type(bq) == "BIGINT[]"

    def test_struct_type(self) -> None:
        bq = {
            "typeKind": "STRUCT",
            "structType": {
                "fields": [
                    {"name": "id", "type": {"typeKind": "INT64"}},
                    {"name": "name", "type": {"typeKind": "STRING"}},
                ],
            },
        }
        rendered = render_duckdb_type(bq)
        assert "id" in rendered
        assert "BIGINT" in rendered
        assert "VARCHAR" in rendered

    def test_missing_type_kind_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="no typeKind"):
            render_duckdb_type({})

    def test_array_without_element_type_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="arrayElementType"):
            render_duckdb_type({"typeKind": "ARRAY"})

    def test_struct_without_fields_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="fields"):
            render_duckdb_type({"typeKind": "STRUCT"})

    def test_struct_field_without_name_raises(self) -> None:
        bq = {"typeKind": "STRUCT", "structType": {"fields": [{"type": {"typeKind": "INT64"}}]}}
        with pytest.raises(InvalidQueryError, match="STRUCT field missing name"):
            render_duckdb_type(bq)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(InvalidQueryError, match="Unsupported routine type"):
            render_duckdb_type({"typeKind": "UNKNOWN_THING"})


class TestPythonToJson:
    def test_passes_through_primitives(self) -> None:
        assert python_to_json_coerce(value=1) == 1
        assert python_to_json_coerce(value="s") == "s"
        assert python_to_json_coerce(value=True) is True
        assert python_to_json_coerce(value=None) is None

    def test_bytes_to_base64(self) -> None:
        assert python_to_json_coerce(value=b"hi") == "aGk="

    def test_decimal_to_string(self) -> None:
        assert python_to_json_coerce(value=Decimal("1.5")) == "1.5"

    def test_datetime_isoformat(self) -> None:
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert python_to_json_coerce(value=dt).startswith("2026-01-01T12:00:00")

    def test_date_isoformat(self) -> None:
        assert python_to_json_coerce(value=date(2026, 4, 15)) == "2026-04-15"

    def test_time_isoformat(self) -> None:
        assert python_to_json_coerce(value=time(10, 30)) == "10:30:00"


class TestParseBqTypeString:
    """Type-string → nested-dict normaliser used by the scripting parser.

    BigQuery's REST API surfaces routine types as nested
    ``StandardSqlDataType`` dicts. The scripting parser captures the
    source-text form (``ARRAY<INT64>``, ``STRUCT<a INT64, b STRING>``)
    instead — this helper normalises the captured text into the dict
    shape every downstream consumer expects.
    """

    def test_scalar_no_precision(self) -> None:
        assert parse_bq_type_string("INT64") == {"typeKind": "INT64"}
        assert parse_bq_type_string("STRING") == {"typeKind": "STRING"}
        assert parse_bq_type_string("bool") == {"typeKind": "BOOL"}

    def test_scalar_with_precision_is_stripped(self) -> None:
        assert parse_bq_type_string("NUMERIC(38, 9)") == {"typeKind": "NUMERIC"}
        assert parse_bq_type_string("DECIMAL(10)") == {"typeKind": "DECIMAL"}

    def test_array_of_scalar(self) -> None:
        assert parse_bq_type_string("ARRAY<INT64>") == {
            "typeKind": "ARRAY",
            "arrayElementType": {"typeKind": "INT64"},
        }

    def test_array_of_array(self) -> None:
        assert parse_bq_type_string("ARRAY<ARRAY<STRING>>") == {
            "typeKind": "ARRAY",
            "arrayElementType": {
                "typeKind": "ARRAY",
                "arrayElementType": {"typeKind": "STRING"},
            },
        }

    def test_struct_simple(self) -> None:
        assert parse_bq_type_string("STRUCT<a INT64, b STRING>") == {
            "typeKind": "STRUCT",
            "structType": {
                "fields": [
                    {"name": "a", "type": {"typeKind": "INT64"}},
                    {"name": "b", "type": {"typeKind": "STRING"}},
                ],
            },
        }

    def test_struct_with_nested_array(self) -> None:
        out = parse_bq_type_string("STRUCT<id INT64, tags ARRAY<STRING>>")
        assert out["typeKind"] == "STRUCT"
        fields = out["structType"]["fields"]
        assert fields[0] == {"name": "id", "type": {"typeKind": "INT64"}}
        assert fields[1]["name"] == "tags"
        assert fields[1]["type"] == {
            "typeKind": "ARRAY",
            "arrayElementType": {"typeKind": "STRING"},
        }

    def test_array_of_struct(self) -> None:
        out = parse_bq_type_string("ARRAY<STRUCT<i INT64, label STRING>>")
        assert out["typeKind"] == "ARRAY"
        assert out["arrayElementType"]["typeKind"] == "STRUCT"
        fields = out["arrayElementType"]["structType"]["fields"]
        assert {f["name"] for f in fields} == {"i", "label"}

    def test_struct_with_backticked_field_name(self) -> None:
        out = parse_bq_type_string("STRUCT<`order` INT64>")
        assert out["structType"]["fields"][0]["name"] == "order"

    def test_malformed_struct_field_raises(self) -> None:
        with pytest.raises(InvalidQueryError):
            parse_bq_type_string("STRUCT<no_type>")
