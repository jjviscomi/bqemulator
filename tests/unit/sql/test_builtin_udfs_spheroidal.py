"""Tests for the spherical-Earth GEOGRAPHY helper UDFs.

The P2.g spheroidal-mapping follow-up shipped four Python helpers that
implement BigQuery's spherical metric semantics on the S2 sphere
(radius ``kEarthRadiusMeters = 6371010.0``):

* ``bqemu_st_distance_spheroidal`` — great-circle distance between
  two POINT inputs, via 3D-unit-vector + ``atan2(|cross|, dot)``.
* ``bqemu_st_length_spheroidal`` — sum of great-circle segments over
  a LINESTRING's consecutive vertices.
* ``bqemu_st_area_spheroidal`` — L'Huilier spherical-excess fan from
  the outer-ring's first vertex; hole rings subtracted.
* ``bqemu_st_perimeter_spheroidal`` — sum of great-circle segments
  around every ring (outer + holes) of a POLYGON.

Each helper is exercised both directly (pure-Python contract) and
through a live DuckDB connection wired by
:func:`bqemulator.sql.builtin_udfs.register_builtin_udfs` to confirm
the engine-side registration matches.
"""

from __future__ import annotations

import math

import duckdb
import pytest

from bqemulator.sql.builtin_udfs import (
    bqemu_st_area_spheroidal,
    bqemu_st_distance_spheroidal,
    bqemu_st_length_spheroidal,
    bqemu_st_perimeter_spheroidal,
    register_builtin_udfs,
)


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    """Live DuckDB connection with every spheroidal helper registered."""
    con = duckdb.connect(":memory:")
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    register_builtin_udfs(con)
    return con


class TestDistance:
    """``bqemu_st_distance_spheroidal``."""

    def test_null_propagates(self) -> None:
        assert bqemu_st_distance_spheroidal(None, "POINT(0 0)") is None
        assert bqemu_st_distance_spheroidal("POINT(0 0)", None) is None

    def test_zero_distance(self) -> None:
        assert bqemu_st_distance_spheroidal("POINT(0 0)", "POINT(0 0)") == pytest.approx(0.0)

    def test_nyc_street_block_matches_recorded(self) -> None:
        """NYC street-block distance matches BigQuery's recording within rel_tol=1e-12."""
        d = bqemu_st_distance_spheroidal(
            "POINT(-73.9855 40.7580)",
            "POINT(-73.9844 40.7580)",
        )
        assert d == pytest.approx(92.65011763880943, rel=1e-12)

    def test_continental_nyc_la_matches_recorded(self) -> None:
        """NYC ↔ LA distance matches BQ's recording within rel_tol=1e-12."""
        d = bqemu_st_distance_spheroidal(
            "POINT(-74.0060 40.7128)",
            "POINT(-118.2437 34.0522)",
        )
        assert d == pytest.approx(3935752.432205476, rel=1e-12)

    def test_non_point_input_returns_null(self) -> None:
        """LINESTRING / POLYGON inputs are not supported and return NULL."""
        assert bqemu_st_distance_spheroidal("LINESTRING(0 0, 1 1)", "POINT(0 0)") is None
        not_a_point = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
        assert bqemu_st_distance_spheroidal("POINT(0 0)", not_a_point) is None

    def test_via_duckdb_engine(self, conn: duckdb.DuckDBPyConnection) -> None:
        """The DuckDB engine-side registration carries the same contract."""
        sql = (
            "SELECT bqemu_st_distance_spheroidal("
            "'POINT(-73.9855 40.7580)', 'POINT(-73.9844 40.7580)')"
        )
        row = conn.execute(sql).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(92.65011763880943, rel=1e-12)


class TestLength:
    """``bqemu_st_length_spheroidal``."""

    def test_null_propagates(self) -> None:
        assert bqemu_st_length_spheroidal(None) is None

    def test_point_returns_zero(self) -> None:
        """BigQuery contract: length of a POINT is 0."""
        assert bqemu_st_length_spheroidal("POINT(0 0)") == 0.0

    def test_polygon_returns_zero(self) -> None:
        """BigQuery contract: length of a POLYGON is 0 (perimeter is a separate function)."""
        assert bqemu_st_length_spheroidal("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))") == 0.0

    def test_two_point_linestring_matches_distance(self) -> None:
        """A 2-vertex linestring's length equals the great-circle distance."""
        length = bqemu_st_length_spheroidal("LINESTRING(-73.9857 40.7580, -73.9857 40.8480)")
        assert length == pytest.approx(10007.559105973656, rel=1e-12)

    def test_multi_segment_linestring_sums_segments(self) -> None:
        """A 3-vertex linestring sums two segments — manual check."""
        # Two equal segments from the equator northwards: 1° + 1°.
        length = bqemu_st_length_spheroidal("LINESTRING(0 0, 0 1, 0 2)")
        # 1° of latitude ≈ 6371010 * pi/180 ≈ 111195.08 m. Sum two of them.
        expected_per_degree = 6371010.0 * math.pi / 180
        assert length == pytest.approx(2 * expected_per_degree, rel=1e-10)


class TestArea:
    """``bqemu_st_area_spheroidal``."""

    def test_null_propagates(self) -> None:
        assert bqemu_st_area_spheroidal(None) is None

    def test_non_polygon_returns_zero(self) -> None:
        """BigQuery contract: area of a POINT or LINESTRING is 0."""
        assert bqemu_st_area_spheroidal("POINT(0 0)") == 0.0
        assert bqemu_st_area_spheroidal("LINESTRING(0 0, 1 1)") == 0.0

    def test_neighborhood_polygon_matches_recorded(self) -> None:
        """A ~1 km² NYC neighborhood polygon's area matches BQ's recording."""
        area = bqemu_st_area_spheroidal(
            "POLYGON((-73.99 40.75, -73.98 40.75, -73.98 40.76, -73.99 40.76, -73.99 40.75))"
        )
        assert area == pytest.approx(936609.46355679, rel=1e-12)

    def test_wyoming_state_polygon_matches_recorded(self) -> None:
        """A ~253,000 km² Wyoming-shaped polygon's area matches BQ's recording."""
        area = bqemu_st_area_spheroidal("POLYGON((-111 41, -104 41, -104 45, -111 45, -111 41))")
        assert area == pytest.approx(253019996794.88388, rel=1e-12)


class TestPerimeter:
    """``bqemu_st_perimeter_spheroidal``."""

    def test_null_propagates(self) -> None:
        assert bqemu_st_perimeter_spheroidal(None) is None

    def test_non_polygon_returns_zero(self) -> None:
        """BigQuery contract: perimeter of a POINT or LINESTRING is 0."""
        assert bqemu_st_perimeter_spheroidal("POINT(0 0)") == 0.0
        assert bqemu_st_perimeter_spheroidal("LINESTRING(0 0, 1 1)") == 0.0

    def test_unit_square_perimeter(self) -> None:
        """A unit-degree square's perimeter is ~four meridian-degrees."""
        perimeter = bqemu_st_perimeter_spheroidal("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        # All four edges run along great-circle arcs of length ~1 deg, ~111195 m.
        # Latitudinal segments (north-south) are exactly that; longitudinal
        # segments compress slightly with latitude. Approximate within 1 %.
        expected_per_degree = 6371010.0 * math.pi / 180
        assert perimeter == pytest.approx(4 * expected_per_degree, rel=1e-2)
