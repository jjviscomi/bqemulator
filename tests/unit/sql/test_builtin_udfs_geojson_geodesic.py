"""Unit tests for the geodesic-interpolation GeoJSON helper UDF.

Pins the contract of
:func:`bqemulator.sql.builtin_udfs.bqemu_geojson_geodesic_interp`
and its supporting helpers
:func:`_spherical_midpoint` /
:func:`_interpolate_edge_geodesic` /
:func:`_walk_geojson_geometry`. The helper closes the 4
``st_asgeojson_*`` XFAILs (P3.d follow-up, 2026-05-19) by inserting
great-circle midpoint vertices into non-equatorial / non-meridian
edges where the geodesic-vs-chord deviation exceeds ~100 µdegrees.
"""

from __future__ import annotations

import json
import math

import pytest

from bqemulator.sql.builtin_udfs import (
    _interpolate_edge_geodesic,
    _interpolate_vertices_geodesic,
    _spherical_midpoint,
    _walk_geojson_geometry,
    bqemu_geojson_geodesic_interp,
)

pytestmark = pytest.mark.unit


class TestSphericalMidpoint:
    """Great-circle midpoint via 3D-unit-vector averaging."""

    def test_equator_midpoint(self) -> None:
        """Points on the equator have linear midpoint == geodesic midpoint."""
        lng, lat = _spherical_midpoint((0.0, 0.0), (1.0, 0.0))
        assert math.isclose(lng, 0.5, abs_tol=1e-10)
        assert math.isclose(lat, 0.0, abs_tol=1e-10)

    def test_meridian_midpoint(self) -> None:
        """Points on a meridian have linear midpoint == geodesic midpoint."""
        lng, lat = _spherical_midpoint((10.0, 0.0), (10.0, 4.0))
        assert math.isclose(lng, 10.0, abs_tol=1e-10)
        assert math.isclose(lat, 2.0, abs_tol=1e-10)

    def test_diagonal_at_low_lat(self) -> None:
        """(0,0)-(1,1): geodesic midpoint matches BigQuery's recorded shape."""
        # BigQuery recorded ST_AsGeoJSON output for this edge does NOT
        # insert a midpoint, but the computed geodesic midpoint is
        # well-defined (slightly off the linear midpoint by ~42 µdeg).
        lng, lat = _spherical_midpoint((0.0, 0.0), (1.0, 1.0))
        assert math.isclose(lng, 0.4999619199226218, rel_tol=1e-12)
        assert math.isclose(lat, 0.5000190382261059, rel_tol=1e-12)

    def test_diagonal_at_higher_lat(self) -> None:
        """(1,1)-(2,2): geodesic midpoint matches BigQuery's recorded vertex."""
        # BigQuery inserts this midpoint into the LineString → the
        # vertex value is the canonical interpolation target.
        lng, lat = _spherical_midpoint((1.0, 1.0), (2.0, 2.0))
        assert math.isclose(lng, 1.4998857365616758, rel_tol=1e-12)
        assert math.isclose(lat, 1.500057091479197, rel_tol=1e-12)

    def test_constant_lat_bows_poleward(self) -> None:
        """Edges along constant latitude bow poleward when off the equator."""
        # (3,3)-(2,3): both at lat=3; geodesic midpoint is slightly
        # poleward (higher latitude in the northern hemisphere).
        _lng, lat = _spherical_midpoint((3.0, 3.0), (2.0, 3.0))
        assert lat > 3.0
        assert math.isclose(lat, 3.00011402647166, rel_tol=1e-12)


class TestInterpolateEdgeGeodesic:
    """Threshold-driven recursive subdivision."""

    def test_equator_edge_no_insert(self) -> None:
        """Equatorial edges (both endpoints on lat=0) skip interpolation."""
        midpoints = _interpolate_edge_geodesic((0.0, 0.0), (1.0, 0.0))
        assert midpoints == []

    def test_meridian_edge_no_insert(self) -> None:
        """Edges along a meridian (constant longitude) skip interpolation."""
        midpoints = _interpolate_edge_geodesic((10.0, 0.0), (10.0, 4.0))
        assert midpoints == []

    def test_low_lat_diagonal_no_insert(self) -> None:
        """(0,0)-(1,1) has ~42 µdeg deviation — below the 100 µdeg threshold."""
        midpoints = _interpolate_edge_geodesic((0.0, 0.0), (1.0, 1.0))
        assert midpoints == []

    def test_constant_lat_2_no_insert(self) -> None:
        """Edge along lat=2 has 76 µdeg deviation — below threshold."""
        midpoints = _interpolate_edge_geodesic((2.0, 2.0), (3.0, 2.0))
        assert midpoints == []

    def test_constant_lat_3_inserts_one_midpoint(self) -> None:
        """Edge along lat=3 has 114 µdeg deviation — above threshold."""
        midpoints = _interpolate_edge_geodesic((3.0, 3.0), (2.0, 3.0))
        assert len(midpoints) == 1
        assert math.isclose(midpoints[0][0], 2.5, abs_tol=1e-12)
        assert math.isclose(midpoints[0][1], 3.00011402647166, rel_tol=1e-12)

    def test_higher_lat_diagonal_inserts_one_midpoint(self) -> None:
        """(1,1)-(2,2) has 128 µdeg deviation — above threshold."""
        midpoints = _interpolate_edge_geodesic((1.0, 1.0), (2.0, 2.0))
        assert len(midpoints) == 1
        assert math.isclose(midpoints[0][0], 1.4998857365616758, rel_tol=1e-12)
        assert math.isclose(midpoints[0][1], 1.500057091479197, rel_tol=1e-12)

    def test_recursive_subdivision_terminates(self) -> None:
        """Sub-edges after one subdivision fall below threshold; no further inserts."""
        # (2,2)-(3,3) has 213 µdeg deviation → first-level insertion;
        # the two sub-edges each span half the original arc with ~1/4
        # the deviation (~53 µdeg) so the recursion bottoms out.
        midpoints = _interpolate_edge_geodesic((2.0, 2.0), (3.0, 3.0))
        assert len(midpoints) == 1


class TestInterpolateVertices:
    """Per-linestring / per-ring interpolation walks every edge in order."""

    def test_short_input(self) -> None:
        """A single-vertex or empty list round-trips unchanged."""
        assert _interpolate_vertices_geodesic([]) == []
        assert _interpolate_vertices_geodesic([[1.0, 2.0]]) == [[1.0, 2.0]]

    def test_linestring_with_mixed_edges(self) -> None:
        """A linestring with one interpolatable + one non-interpolatable edge."""
        vertices = [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
        result = _interpolate_vertices_geodesic(vertices)
        # Edge (0,0)-(1,1): skip (low-lat diagonal). Edge (1,1)-(2,2): insert.
        assert len(result) == 4
        assert result[0] == [0.0, 0.0]
        assert result[1] == [1.0, 1.0]
        assert math.isclose(result[2][0], 1.4998857365616758, rel_tol=1e-12)
        assert math.isclose(result[2][1], 1.500057091479197, rel_tol=1e-12)
        assert result[3] == [2.0, 2.0]


class TestWalkGeojsonGeometry:
    """The geometry-tree walker handles every RFC 7946 type."""

    def test_point_unchanged(self) -> None:
        """Point geometries have no edges; output identical to input."""
        obj = {"type": "Point", "coordinates": [1.0, 2.0]}
        assert _walk_geojson_geometry(obj) == obj

    def test_linestring_interpolated(self) -> None:
        """LineString gets per-edge interpolation applied."""
        obj = {"type": "LineString", "coordinates": [[1.0, 1.0], [2.0, 2.0]]}
        out = _walk_geojson_geometry(obj)
        coords = out["coordinates"]  # type: ignore[index]
        assert len(coords) == 3  # original 2 + 1 midpoint

    def test_polygon_each_ring(self) -> None:
        """Polygon walks each ring; outer + hole rings both interpolate."""
        obj = {
            "type": "Polygon",
            "coordinates": [[[2.0, 3.0], [3.0, 3.0], [3.0, 2.0], [2.0, 2.0], [2.0, 3.0]]],
        }
        out = _walk_geojson_geometry(obj)
        ring = out["coordinates"][0]  # type: ignore[index]
        # Edges: (2,3)-(3,3) along lat=3 → BQ inserts at lat≈3.00011.
        # Other edges are meridian / constant-lat-2 / closing → no insert.
        assert len(ring) == 6  # original 5 + 1 midpoint

    def test_multilinestring_per_linestring(self) -> None:
        """MultiLineString interpolates each child linestring independently."""
        obj = {
            "type": "MultiLineString",
            "coordinates": [
                [[0.0, 0.0], [1.0, 1.0]],
                [[2.0, 2.0], [3.0, 3.0]],
            ],
        }
        out = _walk_geojson_geometry(obj)
        # Line 1: low-lat → no insert (still 2 vertices).
        # Line 2: high-lat diagonal → 1 midpoint inserted (3 vertices).
        assert len(out["coordinates"][0]) == 2  # type: ignore[index]
        assert len(out["coordinates"][1]) == 3  # type: ignore[index]

    def test_geometrycollection_recursive(self) -> None:
        """GeometryCollection walks every child geometry."""
        obj = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [0.0, 0.0]},
                {"type": "LineString", "coordinates": [[1.0, 1.0], [2.0, 2.0]]},
            ],
        }
        out = _walk_geojson_geometry(obj)
        assert out["geometries"][0]["coordinates"] == [0.0, 0.0]  # type: ignore[index]
        assert len(out["geometries"][1]["coordinates"]) == 3  # type: ignore[index]


class TestGeodesicInterpEndToEnd:
    """Full ``bqemu_geojson_geodesic_interp`` round-trip."""

    def test_none_input(self) -> None:
        """``NULL`` propagates through to ``NULL``."""
        assert bqemu_geojson_geodesic_interp(None) is None

    def test_empty_geometry_canonical_form(self) -> None:
        """Empty-geometry normalisation is composed in."""
        result = bqemu_geojson_geodesic_interp('{"type":"Point","coordinates":[]}')
        assert json.loads(result) == {"type": "GeometryCollection", "geometries": []}

    def test_empty_geometrycollection_unchanged(self) -> None:
        """An explicit empty GeometryCollection round-trips canonically."""
        result = bqemu_geojson_geodesic_interp('{"type":"GeometryCollection","geometries":[]}')
        assert json.loads(result) == {"type": "GeometryCollection", "geometries": []}

    def test_invalid_json_passthrough(self) -> None:
        """A non-JSON input round-trips unchanged so the comparator surfaces it."""
        assert bqemu_geojson_geodesic_interp("not json") == "not json"

    def test_linestring_round_trip_matches_bq(self) -> None:
        """LINESTRING(0 0, 1 1, 2 2) → BQ-style geodesic interpolation."""
        raw = '{"type":"LineString","coordinates":[[0.0,0.0],[1.0,1.0],[2.0,2.0]]}'
        result = bqemu_geojson_geodesic_interp(raw)
        obj = json.loads(result)
        assert obj["type"] == "LineString"
        coords = obj["coordinates"]
        assert len(coords) == 4
        # The inserted midpoint matches BigQuery's recorded value to FLOAT64 precision.
        assert math.isclose(coords[2][0], 1.4998857365616758, rel_tol=1e-12)
        assert math.isclose(coords[2][1], 1.500057091479197, rel_tol=1e-12)

    def test_polygon_constant_lat_edges(self) -> None:
        """Polygon with constant-lat edges: only the high-lat edge interpolates."""
        # MULTIPOLYGON test case: second polygon at (2..3, 2..3).
        # Edges along lat=2 (76 µdeg): skip. Edges along lat=3 (114 µdeg): insert.
        raw = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 3.0], [2.0, 2.0]]],
            }
        )
        result = bqemu_geojson_geodesic_interp(raw)
        ring = json.loads(result)["coordinates"][0]
        # 5 original vertices + 1 inserted on the (3,3)-(2,3) edge.
        assert len(ring) == 6
