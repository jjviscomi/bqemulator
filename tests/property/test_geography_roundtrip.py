"""Hypothesis property tests for the GEOGRAPHY codec.

Verifies that any sequence of bytes that DuckDB would emit for a
geometry round-trips through ``wkb_to_wkt`` → ``ST_GeomFromText`` →
``ST_AsWKB`` back to the same WKB (up to the canonical WKB form
DuckDB normalises geometries into).
"""

from __future__ import annotations

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.types.geography import wkb_to_wkt

pytestmark = pytest.mark.property


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    return con


_lon = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
_lat = st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False)


@given(lon=_lon, lat=_lat)
@settings(max_examples=50, deadline=None)
def test_point_wkb_to_wkt_roundtrip(
    lon: float,
    lat: float,
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Any random (lon, lat) point round-trips WKB → WKT → WKB."""
    row = conn.execute("SELECT ST_AsWKB(ST_Point(?, ?))", [lon, lat]).fetchone()
    assert row is not None
    wkb = row[0]
    wkt = wkb_to_wkt(wkb)
    # The WKT must parse back as a geometry equal to the original.
    row2 = conn.execute(
        "SELECT ST_Equals(ST_GeomFromText(?), ST_GeomFromHEXWKB(?))",
        [wkt, wkb.hex()],
    ).fetchone()
    assert row2 == (True,)


@given(
    points=st.lists(
        st.tuples(_lon, _lat),
        min_size=2,
        max_size=5,
    ),
)
@settings(max_examples=30, deadline=None)
def test_linestring_wkb_to_wkt_roundtrip(
    points: list[tuple[float, float]],
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Multi-point LineStrings round-trip through WKB/WKT."""
    points_wkt = ", ".join(f"{lon} {lat}" for lon, lat in points)
    wkt_in = f"LINESTRING({points_wkt})"
    row = conn.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [wkt_in]).fetchone()
    assert row is not None
    wkb = row[0]
    wkt_out = wkb_to_wkt(wkb)
    assert wkt_out.startswith("LINESTRING")
    # The output WKT must parse back to the same geometry.
    row2 = conn.execute(
        "SELECT ST_Equals(ST_GeomFromText(?), ST_GeomFromText(?))",
        [wkt_in, wkt_out],
    ).fetchone()
    assert row2 == (True,)
