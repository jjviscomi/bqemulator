"""Unit tests for the GEOGRAPHY codec and SQL-function mapping."""

from __future__ import annotations

import pytest

from bqemulator.types.geography import (
    BQ_TO_DUCKDB,
    COLLECTION_TYPES,
    DIRECT_MAPPINGS,
    RENAME_MAPPINGS,
    wkb_bytes_to_hex,
    wkb_hex_to_bytes,
    wkb_to_wkt,
)


class TestMappingTables:
    def test_direct_mappings_keys_are_uppercase(self) -> None:
        for m in DIRECT_MAPPINGS:
            assert m.bq_name == m.bq_name.upper(), m

    def test_rename_mappings_target_real_duckdb_names(self) -> None:
        # ST_NPoints / ST_Envelope are present in DuckDB's spatial ext.
        # Catch typos by asserting an expected non-empty target.
        for m in RENAME_MAPPINGS:
            assert m.duckdb_name, m

    def test_bq_to_duckdb_combines_both_tables(self) -> None:
        for m in DIRECT_MAPPINGS:
            assert BQ_TO_DUCKDB[m.bq_name] == m.duckdb_name
        for m in RENAME_MAPPINGS:
            assert BQ_TO_DUCKDB[m.bq_name] == m.duckdb_name

    def test_collection_types_are_uppercase(self) -> None:
        for name in COLLECTION_TYPES:
            assert name == name.upper()

    def test_collection_types_are_disjoint(self) -> None:
        assert len(set(COLLECTION_TYPES)) == len(COLLECTION_TYPES)


class TestWKBHexRoundTrip:
    def test_bytes_to_hex_roundtrip(self) -> None:
        original = bytes([0x01, 0x02, 0x03, 0xFF])
        hex_str = wkb_bytes_to_hex(original)
        assert hex_str == "010203FF"
        assert wkb_hex_to_bytes(hex_str) == original

    def test_hex_accepts_0x_prefix(self) -> None:
        assert wkb_hex_to_bytes("0x01ff") == b"\x01\xff"
        assert wkb_hex_to_bytes("0X01ff") == b"\x01\xff"

    def test_empty(self) -> None:
        assert wkb_bytes_to_hex(b"") == ""
        assert wkb_hex_to_bytes("") == b""


class TestWKBToWKT:
    @pytest.fixture
    def point_wkb(self) -> bytes:
        # Point(1, 2) in little-endian WKB:
        # 01 (LE) 01000000 (type=Point) <8 bytes float 1.0> <8 bytes float 2.0>
        return bytes.fromhex(
            "0101000000000000000000F03F0000000000000040",
        )

    def test_point_wkb_to_wkt(self, point_wkb: bytes) -> None:
        assert wkb_to_wkt(point_wkb) == "POINT (1 2)"

    def test_wkb_to_wkt_rejects_non_bytes(self) -> None:
        with pytest.raises(TypeError, match="expected bytes"):
            wkb_to_wkt("not bytes")  # type: ignore[arg-type]

    def test_wkb_to_wkt_accepts_bytearray(self, point_wkb: bytes) -> None:
        assert wkb_to_wkt(bytearray(point_wkb)) == "POINT (1 2)"
