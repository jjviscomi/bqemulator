"""Tests for the GeoJSON empty-normalisation helper UDF.

``bqemu_geojson_normalize_empty`` rewrites DuckDB-spatial's empty-
coordinates ``ST_AsGeoJSON`` output to the canonical GeoJSON RFC 7946
``GeometryCollection`` form BigQuery emits. Each helper is exercised
both directly (pure-Python contract) and through a live DuckDB
connection wired by :func:`bqemulator.sql.builtin_udfs.register_builtin_udfs`
to confirm the engine-side registration carries the same semantics.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.sql.builtin_udfs import (
    bqemu_geojson_normalize_empty,
    register_builtin_udfs,
)

_EMPTY_GEOJSON = '{"type":"GeometryCollection","geometries":[]}'


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    """Live DuckDB connection with the helper UDF registered."""
    con = duckdb.connect(":memory:")
    register_builtin_udfs(con)
    return con


class TestPurePython:
    """Direct calls against the Python implementation."""

    def test_null_propagates(self) -> None:
        assert bqemu_geojson_normalize_empty(None) is None

    def test_empty_point_becomes_geometrycollection(self) -> None:
        assert bqemu_geojson_normalize_empty('{"type":"Point","coordinates":[]}') == _EMPTY_GEOJSON

    def test_empty_linestring_becomes_geometrycollection(self) -> None:
        assert (
            bqemu_geojson_normalize_empty('{"type":"LineString","coordinates":[]}')
            == _EMPTY_GEOJSON
        )

    def test_empty_polygon_becomes_geometrycollection(self) -> None:
        assert (
            bqemu_geojson_normalize_empty('{"type":"Polygon","coordinates":[]}') == _EMPTY_GEOJSON
        )

    def test_empty_multipoint_becomes_geometrycollection(self) -> None:
        assert (
            bqemu_geojson_normalize_empty('{"type":"MultiPoint","coordinates":[]}')
            == _EMPTY_GEOJSON
        )

    def test_empty_geometrycollection_round_trips_to_canonical(self) -> None:
        # Already a GeometryCollection but with the verbose spacing
        # DuckDB-spatial sometimes emits — normalise to the compact
        # canonical form so the comparison helper sees a uniform shape.
        assert (
            bqemu_geojson_normalize_empty('{"type":"GeometryCollection","geometries":[]}')
            == _EMPTY_GEOJSON
        )

    def test_non_empty_point_preserved(self) -> None:
        payload = '{"type":"Point","coordinates":[3.0,4.0]}'
        assert bqemu_geojson_normalize_empty(payload) == payload

    def test_non_empty_linestring_preserved(self) -> None:
        payload = '{"type":"LineString","coordinates":[[0,0],[1,1]]}'
        assert bqemu_geojson_normalize_empty(payload) == payload

    def test_non_empty_geometrycollection_preserved(self) -> None:
        payload = (
            '{"type":"GeometryCollection","geometries":[{"type":"Point","coordinates":[0,0]}]}'
        )
        assert bqemu_geojson_normalize_empty(payload) == payload

    def test_malformed_json_round_trips(self) -> None:
        # Unparseable input is returned unchanged so the comparison
        # helper can surface the divergence directly rather than this
        # helper silently producing the canonical empty-collection
        # form for arbitrary garbage.
        assert bqemu_geojson_normalize_empty("not json") == "not json"

    def test_non_object_json_round_trips(self) -> None:
        # The helper only acts on JSON *objects*; arrays / scalars pass
        # through unchanged.
        assert bqemu_geojson_normalize_empty("[1,2,3]") == "[1,2,3]"
        assert bqemu_geojson_normalize_empty('"plain string"') == '"plain string"'

    def test_non_empty_coordinates_list_preserved(self) -> None:
        # ``coordinates`` is a list but not empty — preserve.
        payload = '{"type":"Point","coordinates":[0]}'
        assert bqemu_geojson_normalize_empty(payload) == payload


class TestEngineBinding:
    """Smoke tests against the live DuckDB binding."""

    def test_empty_point_engine_binding(self, conn: duckdb.DuckDBPyConnection) -> None:
        row = conn.execute(
            'SELECT bqemu_geojson_normalize_empty(\'{"type":"Point","coordinates":[]}\')'
        ).fetchone()
        assert row == (_EMPTY_GEOJSON,)

    def test_non_empty_engine_binding(self, conn: duckdb.DuckDBPyConnection) -> None:
        payload = '{"type":"Point","coordinates":[3.0,4.0]}'
        row = conn.execute("SELECT bqemu_geojson_normalize_empty(?)", [payload]).fetchone()
        assert row == (payload,)

    def test_null_engine_binding(self, conn: duckdb.DuckDBPyConnection) -> None:
        row = conn.execute("SELECT bqemu_geojson_normalize_empty(CAST(NULL AS VARCHAR))").fetchone()
        assert row == (None,)
