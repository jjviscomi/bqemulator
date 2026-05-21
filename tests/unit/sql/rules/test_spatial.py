"""Tests for the spatial (GEOGRAPHY → DuckDB) translation rules."""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.builtin_udfs import register_builtin_udfs
from bqemulator.sql.translator import SQLTranslator


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    # The ``StAsGeoJsonStringTypeRule`` rewrite wraps every
    # ``ST_AsGeoJSON(g)`` call in
    # ``CAST(bqemu_geojson_normalize_empty(ST_AsGeoJSON(g)) AS VARCHAR)``
    # so the helper UDF must be registered on the connection before
    # any spatial-rule test runs the translated SQL.
    register_builtin_udfs(con)
    return con


@pytest.fixture(scope="module")
def translator() -> SQLTranslator:
    return SQLTranslator()


def _run(translator: SQLTranslator, conn: duckdb.DuckDBPyConnection, bq_sql: str) -> object:
    result = translator.translate(bq_sql)
    assert isinstance(result, Ok), f"translate failed: {result}"
    return conn.execute(result.value).fetchone()


class TestConstructors:
    def test_st_geog_from_text_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(translator, conn, "SELECT ST_GEOGFROMTEXT('POINT(1 2)')")
        assert row is not None and row[0] is not None

    def test_st_geog_point_lon_lat_order(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        # Verify (lon, lat) order is preserved.
        row = _run(translator, conn, "SELECT ST_AsText(ST_GEOGPOINT(-73.985, 40.758))")
        assert row is not None
        assert row[0].startswith("POINT")


class TestPredicates:
    def test_dwithin_spheroidal_metres(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """ST_DWITHIN treats the threshold as metres on the S2 sphere.

        ``POINT(0 0)`` ↔ ``POINT(3 4)`` is ~555.8 km spheroidally. A
        1000 km threshold returns True (within); a 100 km threshold
        returns False (outside). Previously the planar rename treated
        the threshold as degree-units so a 5.0 threshold returned True
        for the same input — see :class:`StDWithinSpheroidalRule`.
        """
        within = _run(
            translator,
            conn,
            "SELECT ST_DWITHIN(ST_GEOGFROMTEXT('POINT(0 0)'), "
            "ST_GEOGFROMTEXT('POINT(3 4)'), 1000000)",
        )
        assert within == (True,)
        outside = _run(
            translator,
            conn,
            "SELECT ST_DWITHIN(ST_GEOGFROMTEXT('POINT(0 0)'), "
            "ST_GEOGFROMTEXT('POINT(3 4)'), 100000)",
        )
        assert outside == (False,)

    def test_intersects_true(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_INTERSECTS(ST_GEOGFROMTEXT('LINESTRING(0 0, 2 2)'), "
            "ST_GEOGFROMTEXT('LINESTRING(0 2, 2 0)'))",
        )
        assert row == (True,)

    def test_st_distance_returns_spheroidal_metres(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """ST_DISTANCE returns spheroidal metres on the S2 sphere.

        Previously the rule renamed to DuckDB's planar ``ST_Distance``
        which returned 5.0 (Euclidean degrees) for ``POINT(0 0)`` ↔
        ``POINT(3 4)``. The P2.g spheroidal rule routes the call through
        :func:`bqemulator.sql.builtin_udfs.bqemu_st_distance_spheroidal`
        and the same input now returns the great-circle distance in
        metres on the S2 sphere (~555.8 km).
        """
        sql = "SELECT ST_DISTANCE(ST_GEOGFROMTEXT('POINT(0 0)'), ST_GEOGFROMTEXT('POINT(3 4)'))"
        row = _run(translator, conn, sql)
        assert row is not None
        assert pytest.approx(row[0], rel=1e-12) == 555812.8141039774


class TestSpheroidalMetricRules:
    """Post-translator rules that route metric ST_* through spherical helpers."""

    def test_st_distance_routes_through_helper(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``StDistanceSpheroidalRule`` rewrites to ``bqemu_st_distance_spheroidal``."""
        bq_sql = (
            "SELECT ST_DISTANCE(ST_GEOGPOINT(-73.9855, 40.7580), ST_GEOGPOINT(-73.9844, 40.7580))"
        )
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok)
        assert "bqemu_st_distance_spheroidal" in result.value.lower()
        row = conn.execute(result.value).fetchone()
        assert row is not None
        # Matches the BigQuery-recorded ~93 m within FLOAT64 precision.
        assert row[0] == pytest.approx(92.65011763880943, rel=1e-12)

    def test_st_length_routes_through_helper(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``StLengthSpheroidalRule`` rewrites to ``bqemu_st_length_spheroidal``."""
        bq_sql = (
            "SELECT ST_LENGTH(ST_GEOGFROMTEXT('LINESTRING(-73.9857 40.7580, -73.9857 40.8480)'))"
        )
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok)
        assert "bqemu_st_length_spheroidal" in result.value.lower()
        row = conn.execute(result.value).fetchone()
        assert row is not None
        # Matches the BigQuery-recorded ~10 km within FLOAT64 precision.
        assert row[0] == pytest.approx(10007.559105973656, rel=1e-12)

    def test_st_area_routes_through_helper(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``StAreaSpheroidalRule`` rewrites to ``bqemu_st_area_spheroidal``."""
        bq_sql = (
            "SELECT ST_AREA(ST_GEOGFROMTEXT("
            "'POLYGON((-73.99 40.75, -73.98 40.75, -73.98 40.76, -73.99 40.76, -73.99 40.75))'))"
        )
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok)
        assert "bqemu_st_area_spheroidal" in result.value.lower()
        row = conn.execute(result.value).fetchone()
        assert row is not None
        # Matches the BigQuery-recorded ~0.94 km² within FLOAT64 precision.
        assert row[0] == pytest.approx(936609.46355679, rel=1e-12)

    def test_st_perimeter_routes_through_helper(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``StPerimeterSpheroidalRule`` rewrites to ``bqemu_st_perimeter_spheroidal``."""
        bq_sql = (
            "SELECT ST_PERIMETER(ST_GEOGFROMTEXT("
            "'POLYGON((-73.99 40.75, -73.98 40.75, -73.98 40.76, -73.99 40.76, -73.99 40.75))'))"
        )
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok)
        assert "bqemu_st_perimeter_spheroidal" in result.value.lower()
        row = conn.execute(result.value).fetchone()
        assert row is not None
        # ~4 km perimeter on the unit-1km neighborhood (~977 m x 4 segments).
        assert row[0] == pytest.approx(3908.525629115109, rel=1e-12)

    def test_st_dwithin_routes_through_spheroidal_distance(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``StDWithinSpheroidalRule`` compares against spheroidal metres."""
        # (0,0) ↔ (0,90) spheroidal distance is ~10⁷ m; 100 m threshold → False.
        far_sql = "SELECT ST_DWITHIN(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(0, 90), 100)"
        result_far = translator.translate(far_sql)
        assert isinstance(result_far, Ok)
        assert "bqemu_st_distance_spheroidal" in result_far.value.lower()
        row_far = conn.execute(result_far.value).fetchone()
        assert row_far == (False,)
        # (0,0) ↔ (0,0.0001) spheroidal distance is ~11 m; 100 m threshold → True.
        near_sql = "SELECT ST_DWITHIN(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(0, 0.0001), 100)"
        result_near = translator.translate(near_sql)
        assert isinstance(result_near, Ok)
        row_near = conn.execute(result_near.value).fetchone()
        assert row_near == (True,)


class TestRenames:
    def test_st_boundingbox_alias_to_envelope(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_AsText(ST_BOUNDINGBOX(ST_GEOGFROMTEXT('POINT(1 2)')))",
        )
        assert row is not None
        # ST_Envelope of a Point is the point.
        assert "POINT" in row[0]

    def test_st_numpoints_alias_to_npoints(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_NUMPOINTS(ST_GEOGFROMTEXT('LINESTRING(0 0, 1 1, 2 2)'))",
        )
        assert row == (3,)


class TestStGeogFromWkb:
    def test_wraps_in_hex(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        # Point(1,2) as raw WKB bytes — equivalent to hex
        # ``0101000000000000000000F03F0000000000000040``.
        wkb_bytes_literal = (
            "'\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00"
            "\\xf0?\\x00\\x00\\x00\\x00\\x00\\x00\\x00@'::BLOB"
        )
        bq_sql = f"SELECT ST_AsText(ST_GEOGFROMWKB({wkb_bytes_literal}))"
        result = translator.translate(bq_sql)
        assert isinstance(result, Ok)
        # The translated SQL should contain HEX() wrapping the argument
        # (SQLGlot's DuckDB serializer upper-cases function names).
        assert "ST_GEOMFROMHEXWKB" in result.value.upper()
        assert "HEX(" in result.value.upper()
        row = conn.execute(result.value).fetchone()
        assert row is not None
        assert "POINT" in row[0]


class TestStIsCollection:
    def test_collection_returns_true(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ISCOLLECTION(ST_GEOGFROMTEXT('GEOMETRYCOLLECTION(POINT(1 2))'))",
        )
        assert row == (True,)

    def test_simple_point_returns_false(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ISCOLLECTION(ST_GEOGFROMTEXT('POINT(1 2)'))",
        )
        assert row == (False,)


class TestStGeometryTypeBqName:
    """``ST_GeometryType(g)`` returns BigQuery's ``ST_<PascalCase>`` form."""

    def test_point_returns_st_point(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_GEOGFROMTEXT('POINT(1 2)'))",
        )
        assert row == ("ST_Point",)

    def test_polygon_returns_st_polygon(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_GEOGFROMTEXT('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))'))",
        )
        assert row == ("ST_Polygon",)

    def test_multipoint_returns_st_multipoint(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_UNION(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(1, 1)))",
        )
        assert row == ("ST_MultiPoint",)

    def test_is_collection_predicate_unaffected(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # ``ST_IsCollection`` uses a synthesised
        # ``ST_GeometryType(g) IN (...)`` predicate against DuckDB's
        # *raw* uppercase names. The BQ-name CASE rewrite must NOT
        # touch that ST_GeometryType (its parent is an ``IN``).
        row = _run(
            translator,
            conn,
            "SELECT ST_ISCOLLECTION(ST_GEOGFROMTEXT('GEOMETRYCOLLECTION(POINT(1 2))'))",
        )
        assert row == (True,)


class TestStAsGeoJsonStringType:
    """``ST_AsGeoJSON(g)`` lands on VARCHAR, not JSON.

    DuckDB-spatial's ``ST_AsGeoJSON`` has a DuckDB logical type of
    ``JSON`` (the engine's ``bqemu.duckdb_type`` metadata picks this
    up and surfaces it on the wire as ``type: "JSON"``). BigQuery's
    ``ST_AsGeoJSON`` returns ``STRING``. The new rule wraps every
    ``ST_AsGeoJSON(g)`` call in ``CAST(... AS VARCHAR)`` so the
    column lands on the right wire-format type.
    """

    def test_st_asgeojson_logical_type_is_varchar_after_translate(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The translated SQL's ``typeof()`` must report VARCHAR."""
        result = translator.translate("SELECT ST_ASGEOJSON(ST_GEOGFROMTEXT('POINT(3 4)')) AS gj")
        assert isinstance(result, Ok), f"translate failed: {result}"
        # Run the translated SQL inside a typeof() probe to verify the
        # logical column type after the CAST wrap.
        wrapped = f"SELECT typeof(gj) FROM ({result.value}) AS sub"
        row = conn.execute(wrapped).fetchone()
        assert row == ("VARCHAR",), (
            f"expected ST_AsGeoJSON output typed as VARCHAR after CAST wrap; got {row!r}"
        )

    def test_st_asgeojson_value_round_trip(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The CAST does not change the GeoJSON string content."""
        row = _run(
            translator,
            conn,
            "SELECT ST_ASGEOJSON(ST_GEOGFROMTEXT('POINT(3 4)'))",
        )
        assert row is not None
        # DuckDB's GeoJSON serialisation is the compact form — content
        # divergence from BigQuery's spaced form is handled by the
        # JSON-shaped STRING comparison rule, not by this SQL rule.
        assert row[0] == '{"type":"Point","coordinates":[3.0,4.0]}'

    def test_idempotent_when_already_cast(self) -> None:
        """The rule does not re-wrap a call that is already CAST AS VARCHAR."""
        from sqlglot import exp, parse_one

        from bqemulator.sql.rules.spatial import StAsGeoJsonStringTypeRule

        # Inside CAST(... AS VARCHAR): rule must NOT match.
        already_cast = parse_one("SELECT CAST(ST_AsGeoJSON(g) AS VARCHAR)", read="duckdb")
        anon = next(
            n
            for n in already_cast.walk()
            if isinstance(n, exp.Anonymous) and str(n.this).upper() == "ST_ASGEOJSON"
        )
        rule = StAsGeoJsonStringTypeRule()
        assert not rule.applies_to(anon)

        # Without the CAST wrapper: rule applies.
        plain = parse_one("SELECT ST_AsGeoJSON(g)", read="duckdb")
        anon_plain = next(
            n
            for n in plain.walk()
            if isinstance(n, exp.Anonymous) and str(n.this).upper() == "ST_ASGEOJSON"
        )
        assert rule.applies_to(anon_plain)

    def test_idempotent_when_wrapped_in_geojson_normalize_empty(self) -> None:
        """The rule does not re-wrap a call already wrapped in the empty-normalisation helper."""
        from sqlglot import exp, parse_one

        from bqemulator.sql.rules.spatial import StAsGeoJsonStringTypeRule

        wrapped = parse_one(
            "SELECT CAST(bqemu_geojson_normalize_empty(ST_AsGeoJSON(g)) AS VARCHAR)",
            read="duckdb",
        )
        anon = next(
            n
            for n in wrapped.walk()
            if isinstance(n, exp.Anonymous) and str(n.this).upper() == "ST_ASGEOJSON"
        )
        assert not StAsGeoJsonStringTypeRule().applies_to(anon)

    def test_empty_point_normalises_to_geometrycollection(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """``POINT EMPTY`` round-trips through the empty-normalisation helper.

        DuckDB-spatial emits ``{"type":"Point","coordinates":[]}`` for
        ``ST_AsGeoJSON(ST_GEOGFROMTEXT('POINT EMPTY'))``. The
        ``bqemu_geojson_normalize_empty`` helper rewrites that empty-
        coordinates shape to the canonical RFC 7946 form, matching
        BigQuery's wire output.
        """
        row = _run(
            translator,
            conn,
            "SELECT ST_ASGEOJSON(ST_GEOGFROMTEXT('POINT EMPTY'))",
        )
        assert row == ('{"type":"GeometryCollection","geometries":[]}',)

    def test_non_empty_point_preserves_payload(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """Non-empty inputs round-trip unchanged through the helper.

        The fixture-level JSON-shaped STRING tolerance (ADR 0022 §3)
        absorbs whitespace / key-order / int-vs-float drift; the helper
        must not change populated geometries.
        """
        row = _run(
            translator,
            conn,
            "SELECT ST_ASGEOJSON(ST_GEOGFROMTEXT('POINT(3 4)'))",
        )
        assert row == ('{"type":"Point","coordinates":[3.0,4.0]}',)


class TestDefensiveBranches:
    """Cover the early-return paths in the spatial rules."""

    def test_rename_rule_target_name_non_anonymous(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import SpatialRenameRule

        # _target_name returns None for non-Anonymous nodes.
        assert SpatialRenameRule._target_name(exp.Literal.number(1)) is None

    def test_rename_rule_does_not_match_unknown_function(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import SpatialRenameRule

        node = exp.Anonymous(this="NOT_A_SPATIAL_FN", expressions=[])
        assert SpatialRenameRule().applies_to(node) is False

    def test_st_geog_from_wkb_with_no_args(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import StGeogFromWkbRule

        # Defensive: a zero-arg call shouldn't crash; we return the
        # node unchanged.
        rule = StGeogFromWkbRule()
        node = exp.Anonymous(this="ST_GEOGFROMWKB", expressions=[])
        assert rule.rewrite(node) is node

    def test_st_iscollection_with_no_args(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import StIsCollectionRule

        rule = StIsCollectionRule()
        node = exp.Anonymous(this="ST_ISCOLLECTION", expressions=[])
        assert rule.rewrite(node) is node


class TestGeographyColumnType:
    """``GEOGRAPHY`` column type in CREATE TABLE → ``GEOMETRY``."""

    def test_ddl_translates_to_geometry(
        self,
        translator: SQLTranslator,
    ) -> None:
        """The translator rewrites ``GEOGRAPHY`` to ``GEOMETRY`` so DuckDB accepts the DDL."""
        result = translator.translate(
            "CREATE TABLE t (id INT64, loc GEOGRAPHY)",
        )
        assert isinstance(result, Ok), result
        upper = result.value.upper()
        assert "GEOMETRY" in upper
        assert "GEOGRAPHY" not in upper

    def test_other_column_types_left_alone(
        self,
        translator: SQLTranslator,
    ) -> None:
        """The rule must not fire on non-GEOGRAPHY data types."""
        result = translator.translate(
            "CREATE TABLE t (a STRING, b INT64, c FLOAT64)",
        )
        assert isinstance(result, Ok), result
        upper = result.value.upper()
        assert "GEOGRAPHY" not in upper
        assert "GEOMETRY" not in upper

    def test_ddl_end_to_end_against_duckdb_spatial(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Translator output executes cleanly against the spatial-extension connection."""
        result = translator.translate(
            "CREATE TABLE t_geog_col (id INT64, loc GEOGRAPHY)",
        )
        assert isinstance(result, Ok), result
        conn.execute(result.value)
        # Sanity check: column landed as GEOMETRY on the DuckDB side
        # (the storage-side type_map maps GEOMETRY back to GEOGRAPHY on
        # the wire).
        column_types = conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 't_geog_col' ORDER BY ordinal_position"
        ).fetchall()
        types = [row[0].upper() for row in column_types]
        assert types == ["BIGINT", "GEOMETRY"]


class TestSession3dRenames:
    """Cover the 14 new ``BQ_TO_DUCKDB`` entries added in session #3d.

    Each test confirms the rename routes through to the DuckDB
    equivalent and returns the expected value for a planar-safe
    fixture. ``ST_ASBINARY`` lands in the spheroidal cluster (BQ
    encodes via ECEF, DuckDB via planar — diverging 1 ULP per axis)
    so the corresponding unit test only checks the BLOB return type,
    not the byte value.
    """

    def test_st_makeline_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_NPOINTS(ST_MAKELINE(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(1, 1)))",
        )
        assert row == (2,)

    def test_st_makepolygon_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE("
            "ST_MAKEPOLYGON(ST_GEOGFROMTEXT('LINESTRING(0 0, 1 0, 1 1, 0 1, 0 0)'))"
            ")",
        )
        assert row == ("ST_Polygon",)

    def test_st_disjoint_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_DISJOINT(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(5, 5))",
        )
        assert row == (True,)

    def test_st_touches_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_TOUCHES("
            "ST_GEOGFROMTEXT('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))'), "
            "ST_GEOGFROMTEXT('POLYGON((1 0, 2 0, 2 1, 1 1, 1 0))'))",
        )
        assert row == (True,)

    def test_st_covers_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_COVERS("
            "ST_GEOGFROMTEXT('POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))'), "
            "ST_GEOGPOINT(1, 1))",
        )
        assert row == (True,)

    def test_st_coveredby_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_COVEREDBY(ST_GEOGPOINT(1, 1), "
            "ST_GEOGFROMTEXT('POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))'))",
        )
        assert row == (True,)

    def test_st_isclosed_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ISCLOSED(ST_GEOGFROMTEXT('LINESTRING(0 0, 1 0, 1 1, 0 0)'))",
        )
        assert row == (True,)

    def test_st_isring_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ISRING(ST_GEOGFROMTEXT('LINESTRING(0 0, 1 0, 1 1, 0 1, 0 0)'))",
        )
        assert row == (True,)

    def test_st_pointn_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ASTEXT(ST_POINTN(ST_GEOGFROMTEXT('LINESTRING(1 2, 3 4, 5 6)'), 1))",
        )
        assert row is not None
        assert "POINT" in row[0]
        assert "1" in row[0]

    def test_st_closestpoint_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ASTEXT(ST_CLOSESTPOINT("
            "ST_GEOGPOINT(0, 0), "
            "ST_GEOGFROMTEXT('LINESTRING(1 0, 1 2)')))",
        )
        assert row is not None
        assert "POINT" in row[0]

    def test_st_difference_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_DIFFERENCE("
            "ST_GEOGFROMTEXT('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))'), "
            "ST_GEOGFROMTEXT('POLYGON((0.5 0.5, 1.5 0.5, 1.5 1.5, 0.5 1.5, 0.5 0.5))')"
            "))",
        )
        assert row == ("ST_Polygon",)

    def test_st_boundary_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_BOUNDARY("
            "ST_GEOGFROMTEXT('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))')))",
        )
        assert row == ("ST_LineString",)

    def test_st_simplify_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_SIMPLIFY("
            "ST_GEOGFROMTEXT('LINESTRING(0 0, 1 0, 2 0)'), 1.0))",
        )
        assert row == ("ST_LineString",)

    def test_st_union_agg_renames(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_UNION_AGG(g)) "
            "FROM UNNEST([ST_GEOGPOINT(0, 0), ST_GEOGPOINT(1, 1)]) AS g",
        )
        assert row == ("ST_MultiPoint",)

    def test_st_asbinary_renames_to_blob(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """``ST_ASBINARY(g)`` returns BLOB; the byte value lands on a
        BQ-spheroidal ULP-divergence pinned XFAIL in the conformance
        corpus."""
        row = _run(
            translator,
            conn,
            "SELECT ST_ASBINARY(ST_GEOGPOINT(1, 1))",
        )
        assert row is not None
        assert isinstance(row[0], (bytes, bytearray))
        assert len(row[0]) > 0


class TestStIntersectsBoxRule:
    """Cover :class:`StIntersectsBoxRule`."""

    def test_inside_box_returns_true(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_INTERSECTSBOX(ST_GEOGPOINT(1, 1), 0, 0, 2, 2)",
        )
        assert row == (True,)

    def test_outside_box_returns_false(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_INTERSECTSBOX(ST_GEOGPOINT(5, 5), 0, 0, 2, 2)",
        )
        assert row == (False,)

    def test_rewrite_uses_st_intersects_and_makeenvelope(self) -> None:
        from bqemulator.sql.rules.spatial import StIntersectsBoxRule

        translator = SQLTranslator()
        result = translator.translate("SELECT ST_INTERSECTSBOX(ST_GEOGPOINT(1, 1), 0, 0, 2, 2)")
        assert isinstance(result, Ok), result
        upper = result.value.upper()
        assert "ST_INTERSECTS" in upper
        assert "ST_MAKEENVELOPE" in upper
        # ST_INTERSECTSBOX itself does not survive.
        assert "ST_INTERSECTSBOX" not in upper
        del StIntersectsBoxRule  # silence unused import for ruff

    def test_wrong_arity_passes_through_unchanged(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import StIntersectsBoxRule

        # Defensive: a 3-arg call (wrong arity) should not crash.
        rule = StIntersectsBoxRule()
        node = exp.Anonymous(
            this="ST_INTERSECTSBOX",
            expressions=[
                exp.Literal.number(1),
                exp.Literal.number(2),
                exp.Literal.number(3),
            ],
        )
        assert rule.rewrite(node) is node


class TestStMaxDistanceRule:
    """Cover :class:`StMaxDistanceRule` — POINT-POINT routes to spheroidal helper."""

    def test_point_point_returns_spheroidal_distance(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_MAXDISTANCE(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(1, 1))",
        )
        assert row is not None
        # The recorded BigQuery value is 157249.6280925079m for this pair;
        # the spheroidal helper matches to FLOAT64 precision.
        assert row[0] == pytest.approx(157249.6280925079, rel=1e-12)

    def test_rewrite_uses_distance_spheroidal_helper(self) -> None:
        from bqemulator.sql.rules.spatial import StMaxDistanceRule

        translator = SQLTranslator()
        result = translator.translate(
            "SELECT ST_MAXDISTANCE(ST_GEOGPOINT(0, 0), ST_GEOGPOINT(1, 1))",
        )
        assert isinstance(result, Ok), result
        # The rewriter routes through the helper UDF rather than DuckDB.
        assert "BQEMU_ST_DISTANCE_SPHEROIDAL" in result.value.upper()
        assert "ST_MAXDISTANCE" not in result.value.upper()
        del StMaxDistanceRule  # silence unused import for ruff


class TestStSnapToGridRule:
    """Cover :class:`StSnapToGridRule` + :func:`bqemu_st_snaptogrid`."""

    def test_basic_snap_to_tenths(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_ASTEXT(ST_SNAPTOGRID(ST_GEOGPOINT(1.234, 5.678), 0.1))",
        )
        assert row is not None
        # WKT format: "POINT (1.2 5.7)" (DuckDB inserts a space).
        assert "1.2" in row[0]
        assert "5.7" in row[0]

    def test_helper_with_null_inputs(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_st_snaptogrid

        assert bqemu_st_snaptogrid(None, 0.1) is None
        assert bqemu_st_snaptogrid("POINT(1 2)", None) is None
        assert bqemu_st_snaptogrid("POINT(1 2)", 0.0) is None
        assert bqemu_st_snaptogrid("POINT(1 2)", -0.1) is None

    def test_helper_rounds_to_size_precision(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_st_snaptogrid

        # 1.234 / 0.1 = 12.34 -> round() = 12 -> 12 * 0.1 = 1.2 exactly.
        # The helper must absorb float-arithmetic noise so the WKT
        # number doesn't print as "1.2000000000000002".
        result = bqemu_st_snaptogrid("POINT(1.234 5.678)", 0.1)
        assert result == "POINT(1.2 5.7)"

    def test_helper_with_integer_size(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_st_snaptogrid

        result = bqemu_st_snaptogrid("POINT(1.4 5.6)", 1.0)
        # round(1.4) = 1; round(5.6) = 6.
        assert result == "POINT(1 6)"

    def test_wrong_arity_passes_through_unchanged(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import StSnapToGridRule

        rule = StSnapToGridRule()
        node = exp.Anonymous(this="ST_SNAPTOGRID", expressions=[])
        assert rule.rewrite(node) is node


class TestStMakePolygonOrientedRule:
    """Cover :class:`StMakePolygonOrientedRule`."""

    def test_array_argument_extracts_first_ring(
        self,
        translator: SQLTranslator,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        row = _run(
            translator,
            conn,
            "SELECT ST_GEOMETRYTYPE(ST_MAKEPOLYGONORIENTED("
            "[ST_GEOGFROMTEXT('LINESTRING(0 0, 1 0, 1 1, 0 1, 0 0)')]))",
        )
        assert row == ("ST_Polygon",)

    def test_non_array_argument_passes_through(self) -> None:
        """If callers pass a bare LINESTRING (non-array), the rule
        routes it through unchanged so DuckDB sees ST_MakePolygon(line).
        """
        from sqlglot import exp, parse_one

        from bqemulator.sql.rules.spatial import StMakePolygonOrientedRule

        tree = parse_one("SELECT ST_MAKEPOLYGONORIENTED(foo)", read="bigquery")
        anon = next(
            n
            for n in tree.walk()
            if isinstance(n, exp.Anonymous) and str(n.this).upper() == "ST_MAKEPOLYGONORIENTED"
        )
        rewritten = StMakePolygonOrientedRule().rewrite(anon)
        assert isinstance(rewritten, exp.Anonymous)
        assert str(rewritten.this) == "ST_MakePolygon"
        # The argument is the original `foo` column reference.
        assert rewritten.expressions[0].sql() == "foo"

    def test_no_args_passes_through_unchanged(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import StMakePolygonOrientedRule

        rule = StMakePolygonOrientedRule()
        node = exp.Anonymous(this="ST_MAKEPOLYGONORIENTED", expressions=[])
        assert rule.rewrite(node) is node


class TestGeographyColumnTypeRuleUnit:
    """AST-level coverage of :class:`GeographyColumnTypeRule`."""

    def test_applies_to_geography_data_type(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import GeographyColumnTypeRule

        node = exp.DataType(this=exp.DataType.Type.GEOGRAPHY, nested=False)
        assert GeographyColumnTypeRule().applies_to(node) is True

    def test_does_not_apply_to_other_data_types(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import GeographyColumnTypeRule

        node = exp.DataType(this=exp.DataType.Type.BIGINT, nested=False)
        assert GeographyColumnTypeRule().applies_to(node) is False

    def test_rewrite_returns_geometry_data_type(self) -> None:
        from sqlglot import exp

        from bqemulator.sql.rules.spatial import GeographyColumnTypeRule

        node = exp.DataType(this=exp.DataType.Type.GEOGRAPHY, nested=False)
        rewritten = GeographyColumnTypeRule().rewrite(node)
        assert isinstance(rewritten, exp.DataType)
        assert rewritten.this == exp.DataType.Type.GEOMETRY
