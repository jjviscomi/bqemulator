"""Unit tests for ``types.range_type``."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.types.range_type import (
    END_FIELD,
    START_FIELD,
    VALID_ELEMENT_TYPES,
    detect_range_element,
    duckdb_struct_for,
    parse_bq_range_type,
    validate_element_type,
)


class TestValidateElementType:
    @pytest.mark.parametrize("name", ["DATE", "DATETIME", "TIMESTAMP"])
    def test_accepts_each_valid_type(self, name: str) -> None:
        assert validate_element_type(name) == name

    def test_uppercases_and_strips(self) -> None:
        assert validate_element_type("  date  ") == "DATE"
        assert validate_element_type("DateTime") == "DATETIME"

    @pytest.mark.parametrize("name", ["TIME", "INT64", "STRING", "RANGE", "", " "])
    def test_rejects_invalid(self, name: str) -> None:
        with pytest.raises(ValidationError, match="RANGE element type"):
            validate_element_type(name)


class TestDuckdbStructFor:
    def test_date_produces_date_struct(self) -> None:
        spec = duckdb_struct_for("DATE")
        assert spec == 'STRUCT("start" DATE, "end" DATE)'

    def test_datetime_maps_to_naive_timestamp(self) -> None:
        spec = duckdb_struct_for("DATETIME")
        assert spec == 'STRUCT("start" TIMESTAMP, "end" TIMESTAMP)'

    def test_timestamp_maps_to_timestamptz(self) -> None:
        spec = duckdb_struct_for("TIMESTAMP")
        assert spec == 'STRUCT("start" TIMESTAMPTZ, "end" TIMESTAMPTZ)'

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValidationError):
            duckdb_struct_for("FLOAT64")

    def test_field_names_are_constants(self) -> None:
        # Compile-time guarantee tests use these constants.
        assert START_FIELD == "start"
        assert END_FIELD == "end"


class TestParseBqRangeType:
    @pytest.mark.parametrize(
        ("inp", "expected"),
        [
            ("RANGE<DATE>", "DATE"),
            ("RANGE<DATETIME>", "DATETIME"),
            ("RANGE<TIMESTAMP>", "TIMESTAMP"),
            ("range<date>", "DATE"),
            ("  RANGE< DATE >  ", "DATE"),
        ],
    )
    def test_parses_each_form(self, inp: str, expected: str) -> None:
        assert parse_bq_range_type(inp) == expected

    @pytest.mark.parametrize(
        "inp",
        [
            "RANGE",  # missing angle brackets
            "RANGE<>",
            "RANGE<DATE",
            "DATE>",
            "RANGE DATE",
            "RANGE<FLOAT64>",  # invalid element
        ],
    )
    def test_rejects_malformed(self, inp: str) -> None:
        with pytest.raises(ValidationError):
            parse_bq_range_type(inp)


def test_valid_element_types_locked() -> None:
    """Lock the tuple — BigQuery's three valid RANGE element types."""
    assert VALID_ELEMENT_TYPES == ("DATE", "DATETIME", "TIMESTAMP")


class TestDetectRangeElement:
    """ADR 0023 §1.G: recover BigQuery RANGE element type from a DuckDB column type."""

    @pytest.mark.parametrize(
        ("duckdb_type", "expected"),
        [
            ('STRUCT("start" DATE, "end" DATE)', ("DATE", False)),
            ('STRUCT("start" TIMESTAMP, "end" TIMESTAMP)', ("DATETIME", False)),
            (
                'STRUCT("start" TIMESTAMP WITH TIME ZONE, "end" TIMESTAMP WITH TIME ZONE)',
                ("TIMESTAMP", False),
            ),
            ('STRUCT("start" DATE, "end" DATE)[]', ("DATE", True)),
            ('STRUCT("start" TIMESTAMP, "end" TIMESTAMP)[]', ("DATETIME", True)),
            (
                'STRUCT("start" TIMESTAMP WITH TIME ZONE, "end" TIMESTAMP WITH TIME ZONE)[]',
                ("TIMESTAMP", True),
            ),
        ],
    )
    def test_matches_each_canonical_shape(
        self,
        duckdb_type: str,
        expected: tuple[str, bool],
    ) -> None:
        assert detect_range_element(duckdb_type) == expected

    def test_case_insensitive(self) -> None:
        assert detect_range_element('struct("start" date, "end" date)') == ("DATE", False)

    @pytest.mark.parametrize(
        "duckdb_type",
        [
            None,
            "",
            "INTEGER",
            "VARCHAR",
            # Wrong field names — looks like a struct but not the RANGE shape.
            'STRUCT("low" DATE, "high" DATE)',
            # Heterogeneous inner types — not a RANGE shape.
            'STRUCT("start" DATE, "end" TIMESTAMP)',
            # Inner type not in {DATE, TIMESTAMP, TIMESTAMP WITH TIME ZONE}.
            'STRUCT("start" INTEGER, "end" INTEGER)',
            # Extra fields beyond start/end — not a RANGE.
            'STRUCT("start" DATE, "end" DATE, "x" INTEGER)',
        ],
    )
    def test_negative_matches_return_none(self, duckdb_type: str | None) -> None:
        assert detect_range_element(duckdb_type) is None
