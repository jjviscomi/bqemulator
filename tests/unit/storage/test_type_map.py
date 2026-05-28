"""Unit tests for the BigQuery ↔ DuckDB type mapper."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.storage.type_map import (
    bq_schema_to_duckdb_columns,
    bq_to_duckdb,
    duckdb_to_bq,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# bq_to_duckdb — scalar types
# ---------------------------------------------------------------------------


class TestBqToDuckdbScalar:
    @pytest.mark.parametrize(
        ("bq", "expected"),
        [
            ("INT64", "BIGINT"),
            ("FLOAT64", "DOUBLE"),
            ("NUMERIC", "DECIMAL(38, 9)"),
            ("BIGNUMERIC", "DECIMAL(76, 38)"),
            ("BOOL", "BOOLEAN"),
            ("STRING", "VARCHAR"),
            ("BYTES", "BLOB"),
            ("DATE", "DATE"),
            ("TIME", "TIME"),
            ("DATETIME", "TIMESTAMP"),
            ("TIMESTAMP", "TIMESTAMPTZ"),
            ("JSON", "JSON"),
        ],
    )
    def test_scalar_mapping(self, bq: str, expected: str) -> None:
        assert bq_to_duckdb(bq) == expected

    def test_case_insensitive(self) -> None:
        assert bq_to_duckdb("int64") == "BIGINT"
        assert bq_to_duckdb("  String  ") == "VARCHAR"

    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("INTEGER", "BIGINT"),
            ("integer", "BIGINT"),
            ("FLOAT", "DOUBLE"),
            ("BOOLEAN", "BOOLEAN"),
            ("boolean", "BOOLEAN"),
        ],
    )
    def test_legacy_aliases_accepted(self, legacy: str, expected: str) -> None:
        """Real BigQuery accepts legacy names from older clients; we do too."""
        assert bq_to_duckdb(legacy) == expected

    def test_unknown_type_raises(self) -> None:
        # Phase 9 wired up GEOGRAPHY — pick a name that is still
        # genuinely unknown so the unknown-type assertion stays
        # honest.
        with pytest.raises(ValidationError, match="Unknown BigQuery type"):
            bq_to_duckdb("UNICORN")


class TestBqToDuckdbParameterized:
    def test_array_of_int(self) -> None:
        assert bq_to_duckdb("ARRAY<INT64>") == "BIGINT[]"

    def test_array_of_string(self) -> None:
        assert bq_to_duckdb("ARRAY<STRING>") == "VARCHAR[]"

    def test_array_of_struct(self) -> None:
        result = bq_to_duckdb("ARRAY<STRUCT<name STRING, age INT64>>")
        assert result == "STRUCT(name VARCHAR, age BIGINT)[]"

    def test_struct_simple(self) -> None:
        result = bq_to_duckdb("STRUCT<name STRING, age INT64>")
        assert result == "STRUCT(name VARCHAR, age BIGINT)"

    def test_nested_struct(self) -> None:
        result = bq_to_duckdb("STRUCT<address STRUCT<street STRING, city STRING>>")
        assert "STRUCT(street VARCHAR, city VARCHAR)" in result


# ---------------------------------------------------------------------------
# duckdb_to_bq — scalar types
# ---------------------------------------------------------------------------


class TestDuckdbToBqScalar:
    @pytest.mark.parametrize(
        ("duckdb", "expected"),
        [
            ("BIGINT", "INT64"),
            ("DOUBLE", "FLOAT64"),
            ("BOOLEAN", "BOOL"),
            ("VARCHAR", "STRING"),
            ("BLOB", "BYTES"),
            ("DATE", "DATE"),
            ("TIME", "TIME"),
            ("TIMESTAMP", "DATETIME"),
            ("TIMESTAMPTZ", "TIMESTAMP"),
            ("TIMESTAMP WITH TIME ZONE", "TIMESTAMP"),
            ("JSON", "JSON"),
        ],
    )
    def test_scalar_mapping(self, duckdb: str, expected: str) -> None:
        assert duckdb_to_bq(duckdb) == expected

    def test_aliases(self) -> None:
        assert duckdb_to_bq("INTEGER") == "INT64"
        assert duckdb_to_bq("TEXT") == "STRING"
        assert duckdb_to_bq("REAL") == "FLOAT64"

    def test_decimal_to_numeric(self) -> None:
        assert duckdb_to_bq("DECIMAL(38, 9)") == "NUMERIC"
        assert duckdb_to_bq("DECIMAL(18, 0)") == "NUMERIC"

    def test_large_decimal_to_bignumeric(self) -> None:
        assert duckdb_to_bq("DECIMAL(76, 38)") == "BIGNUMERIC"

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="Unmappable DuckDB type"):
            duckdb_to_bq("SUPERNOVA")


class TestDuckdbToBqParameterized:
    def test_array_suffix(self) -> None:
        assert duckdb_to_bq("BIGINT[]") == "ARRAY<INT64>"

    def test_list_function(self) -> None:
        assert duckdb_to_bq("LIST(BIGINT)") == "ARRAY<INT64>"

    def test_struct_function(self) -> None:
        result = duckdb_to_bq("STRUCT(name VARCHAR, age BIGINT)")
        assert result == "STRUCT<name STRING, age INT64>"

    def test_list_function_spaced(self) -> None:
        # A space before the paren is tolerated; both forms resolve alike.
        assert duckdb_to_bq("LIST (BIGINT)") == "ARRAY<INT64>"

    def test_struct_function_spaced(self) -> None:
        result = duckdb_to_bq("STRUCT (name VARCHAR, age BIGINT)")
        assert result == "STRUCT<name STRING, age INT64>"


# ---------------------------------------------------------------------------
# Round-trip invariant
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.parametrize(
        "bq_type",
        [
            "INT64",
            "FLOAT64",
            "NUMERIC",
            "BIGNUMERIC",
            "BOOL",
            "STRING",
            "BYTES",
            "DATE",
            "TIME",
            "DATETIME",
            "TIMESTAMP",
            "JSON",
        ],
    )
    def test_scalar_round_trip(self, bq_type: str) -> None:
        duckdb_type = bq_to_duckdb(bq_type)
        assert duckdb_to_bq(duckdb_type) == bq_type


# ---------------------------------------------------------------------------
# Schema-level helper
# ---------------------------------------------------------------------------


class TestBqSchemaToDuckdbColumns:
    def test_simple_schema(self) -> None:
        fields = [
            {"name": "id", "type": "INT64", "mode": "REQUIRED"},
            {"name": "amount", "type": "NUMERIC"},
            {"name": "created", "type": "TIMESTAMP"},
        ]
        cols = bq_schema_to_duckdb_columns(fields)
        assert cols == [
            ("id", "BIGINT"),
            ("amount", "DECIMAL(38, 9)"),
            ("created", "TIMESTAMPTZ"),
        ]

    def test_repeated_mode_wraps_in_list(self) -> None:
        fields = [{"name": "tags", "type": "STRING", "mode": "REPEATED"}]
        cols = bq_schema_to_duckdb_columns(fields)
        assert cols == [("tags", "VARCHAR[]")]

    def test_record_type_becomes_struct(self) -> None:
        fields = [
            {
                "name": "address",
                "type": "RECORD",
                "fields": [
                    {"name": "street", "type": "STRING"},
                    {"name": "city", "type": "STRING"},
                ],
            },
        ]
        cols = bq_schema_to_duckdb_columns(fields)
        assert cols == [("address", "STRUCT(street VARCHAR, city VARCHAR)")]

    def test_repeated_record(self) -> None:
        fields = [
            {
                "name": "items",
                "type": "RECORD",
                "mode": "REPEATED",
                "fields": [
                    {"name": "sku", "type": "STRING"},
                    {"name": "qty", "type": "INT64"},
                ],
            },
        ]
        cols = bq_schema_to_duckdb_columns(fields)
        assert cols == [("items", "STRUCT(sku VARCHAR, qty BIGINT)[]")]


class TestSpecializedTypes:
    """Type-map coverage for GEOGRAPHY / INTERVAL / RANGE."""

    def test_geography_to_geometry(self) -> None:
        assert bq_to_duckdb("GEOGRAPHY") == "GEOMETRY"

    def test_geometry_to_geography_reverse(self) -> None:
        from bqemulator.storage.type_map import duckdb_to_bq

        assert duckdb_to_bq("GEOMETRY") == "GEOGRAPHY"

    def test_interval_round_trip(self) -> None:
        from bqemulator.storage.type_map import duckdb_to_bq

        assert bq_to_duckdb("INTERVAL") == "INTERVAL"
        assert duckdb_to_bq("INTERVAL") == "INTERVAL"

    def test_range_date_expands_to_struct(self) -> None:
        result = bq_to_duckdb("RANGE<DATE>")
        assert result == 'STRUCT("start" DATE, "end" DATE)'

    def test_range_timestamp_uses_tz(self) -> None:
        result = bq_to_duckdb("RANGE<TIMESTAMP>")
        assert result == 'STRUCT("start" TIMESTAMPTZ, "end" TIMESTAMPTZ)'

    def test_range_datetime_is_naive(self) -> None:
        result = bq_to_duckdb("RANGE<DATETIME>")
        assert result == 'STRUCT("start" TIMESTAMP, "end" TIMESTAMP)'

    def test_range_schema_field_round_trip(self) -> None:
        fields = [
            {
                "name": "duration",
                "type": "RANGE",
                "mode": "NULLABLE",
                "rangeElementType": {"type": "DATE"},
            },
        ]
        cols = bq_schema_to_duckdb_columns(fields)
        assert cols == [("duration", 'STRUCT("start" DATE, "end" DATE)')]

    def test_range_field_missing_element_type_raises(self) -> None:
        fields = [{"name": "duration", "type": "RANGE", "mode": "NULLABLE"}]
        with pytest.raises(ValidationError, match="missing rangeElementType"):
            bq_schema_to_duckdb_columns(fields)

    def test_range_field_missing_element_type_inner_type_raises(self) -> None:
        fields = [
            {
                "name": "duration",
                "type": "RANGE",
                "mode": "NULLABLE",
                "rangeElementType": {},
            },
        ]
        with pytest.raises(ValidationError, match="missing 'type'"):
            bq_schema_to_duckdb_columns(fields)
