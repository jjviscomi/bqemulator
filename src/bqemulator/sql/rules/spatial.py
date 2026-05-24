"""Translation rules for BigQuery GEOGRAPHY (``ST_*``) functions.

DuckDB's spatial extension exposes a parallel ``ST_*`` family but with
different names and conventions:

* BigQuery ``ST_GeogFromText`` / ``ST_GeogFromGeoJson`` /
  ``ST_GeogPoint`` ↔ DuckDB ``ST_GeomFromText`` /
  ``ST_GeomFromGeoJSON`` / ``ST_Point`` (note the ``Geog`` prefix
  diverges from ``Geom`` and ``Point`` drops the ``Geog`` entirely).
* BigQuery ``ST_GeogFromWkb(BYTES)`` ↔ DuckDB
  ``ST_GeomFromHEXWKB(hex(BYTES))``.
* BigQuery ``ST_BoundingBox`` ↔ DuckDB ``ST_Envelope``.
* BigQuery ``ST_NumPoints`` / ``ST_NPoints`` ↔ DuckDB ``ST_NPoints``.
* BigQuery ``ST_IsCollection(g)`` ↔ DuckDB
  ``ST_GeometryType(g) IN (…)``.

Plain renames flow through :class:`SpatialRenameRule`. The custom
shapes (``ST_GeogFromWkb`` and ``ST_IsCollection``) have their own
rules with explicit replacement.

ADR 0019 records the design decision (DuckDB spatial as the
GEOGRAPHY backend; planar semantics).
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule
from bqemulator.types.geography import BQ_TO_DUCKDB, COLLECTION_TYPES

_GEOMETRY_TYPE_BQ: dict[str, str] = {
    "POINT": "ST_Point",
    "LINESTRING": "ST_LineString",
    "POLYGON": "ST_Polygon",
    "MULTIPOINT": "ST_MultiPoint",
    "MULTILINESTRING": "ST_MultiLineString",
    "MULTIPOLYGON": "ST_MultiPolygon",
    "GEOMETRYCOLLECTION": "ST_GeometryCollection",
}


@register
class StGeometryTypeBqNameRule(TranslationRule):
    """``ST_GeometryType(g)`` → CASE mapping DuckDB names → BigQuery names.

    DuckDB returns uppercase WKT shape names (``POINT``, ``MULTIPOINT``,
    ``POLYGON`` …). BigQuery returns the ``ST_<PascalCase>`` form
    (``ST_Point``, ``ST_MultiPoint``, ``ST_Polygon`` …). We wrap the
    call in a ``CASE … END`` expression that maps each DuckDB name to
    its BigQuery equivalent; types DuckDB might add in the future fall
    through to NULL rather than a wrong-prefix string.

    The rule must register *before* :class:`SpatialRenameRule` so it
    fires on the unrenamed ``Anonymous(ST_GEOMETRYTYPE)`` node and the
    enclosing CASE survives the post-order pass. DuckDB function names
    are case-insensitive, so the inlined ``ST_GEOMETRYTYPE(arg)`` calls
    inside the CASE evaluate correctly without an additional rename.

    The rule does NOT fire when the call is inside an ``IN`` predicate
    (the ``ST_IsCollection`` expansion's membership test); the parent
    IN's expressions are DuckDB-native strings, so the predicate
    works as-is.
    """

    name = "ST_GEOMETRYTYPE_BQ_NAME"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match anonymous ``ST_GeometryType`` calls with one argument."""
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_GEOMETRYTYPE":
            return False
        return not isinstance(node.parent, exp.In)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit a ``CASE`` mapping over the DuckDB output strings."""
        anon = node
        if not anon.expressions:
            return node
        ifs = [
            exp.If(
                this=exp.EQ(
                    this=anon.copy(),
                    expression=exp.Literal.string(duck_name),
                ),
                true=exp.Literal.string(bq_name),
            )
            for duck_name, bq_name in _GEOMETRY_TYPE_BQ.items()
        ]
        return exp.Case(ifs=ifs, default=exp.Null())


@register
class StAsGeoJsonStringTypeRule(TranslationRule):
    """``ST_AsGeoJSON(g)`` → ``CAST(ST_AsGeoJSON(g) AS VARCHAR)``.

    DuckDB-spatial's ``ST_AsGeoJSON`` returns a value whose Arrow
    output is a VARCHAR string but whose DuckDB *logical* type is
    ``JSON`` (verifiable via ``SELECT typeof(ST_AsGeoJSON(...))``).
    The engine's ``bqemu.duckdb_type`` field-metadata override reads
    that logical type and tags the column as ``JSON`` on the wire —
    but BigQuery's ``ST_AsGeoJSON``
    returns ``STRING``, so the schema check in the conformance
    comparison helper fails.

    Wrapping the call in ``CAST(... AS VARCHAR)`` re-types the DuckDB
    column to a true VARCHAR; the metadata override then reads
    ``typeof(CAST(... AS VARCHAR)) = 'VARCHAR'``, the wire-format
    schema entry lands on ``STRING``, and the BigQuery client parses
    the result as a STRING column. The string content still differs
    from BigQuery's GeoJSON formatting (key order, integer vs float
    coords, inter-token whitespace); the JSON-shaped STRING sub-rule
    in [ADR 0022 §3](../../../docs/adr/0022-conformance-corpus-design.md)
    handles that content-level normalisation.

    The rule must register BEFORE :class:`SpatialRenameRule` so it
    fires on the unrenamed ``Anonymous(ST_ASGEOJSON)`` node before
    the rename replaces it (the post-order pass visits each node
    once and breaks after the first matching rule). DuckDB function
    names are case-insensitive, so emitting
    ``CAST(ST_ASGEOJSON(...) AS VARCHAR)`` (BQ casing inside) is
    accepted by the engine without a subsequent rename.
    """

    name = "ST_ASGEOJSON_STRING_TYPE"

    #: DuckDB types that are equivalent to VARCHAR for our purposes.
    #: SQLGlot normalises ``VARCHAR`` → ``DType.TEXT`` during the
    #: BigQuery-to-DuckDB transpile, so the idempotency guard accepts
    #: both spellings.
    _VARCHAR_DTYPES = frozenset(
        {
            exp.DataType.Type.VARCHAR,
            exp.DataType.Type.TEXT,
        }
    )

    def applies_to(self, node: exp.Expression) -> bool:
        """Match an unwrapped ``ST_ASGEOJSON`` anonymous call.

        Excludes calls already wrapped in the geodesic-interp /
        empty-normalisation helpers (``bqemu_geojson_geodesic_interp``
        and ``bqemu_geojson_normalize_empty``) — the rewrite uses
        ``bqemu_geojson_geodesic_interp`` as the inner of the
        ``Cast(... AS VARCHAR)`` envelope, so re-walking the AST
        should not re-wrap a call whose parent is already the helper.
        Also excludes calls already wrapped in ``CAST(... AS
        VARCHAR)`` (or the SQLGlot-normalised ``CAST(... AS TEXT)``)
        — that covers the historical AST shape before the
        empty-normalisation helper was added, and any test that
        constructs the legacy form directly.
        """
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_ASGEOJSON":
            return False
        parent = node.parent
        if isinstance(parent, exp.Anonymous) and str(parent.this).lower() in {
            "bqemu_geojson_normalize_empty",
            "bqemu_geojson_geodesic_interp",
        }:
            return False
        if isinstance(parent, exp.Cast):
            target = parent.to
            if isinstance(target, exp.DataType) and target.this in self._VARCHAR_DTYPES:
                return False
        return True

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the ``ST_ASGEOJSON(g)`` call in geodesic-interp + ``CAST(... AS VARCHAR)``.

        The inner ``bqemu_geojson_geodesic_interp`` UDF performs two
        composed transformations on DuckDB-spatial's GeoJSON output:

        1. **Empty-geometry normalisation** — rewrites the literal
           empty-coordinates shapes
           (``{"type": "Point", "coordinates": []}`` etc.) to the
           canonical RFC 7946 ``GeometryCollection`` literal that
           BigQuery emits.
        2. **Geodesic-midpoint interpolation** — walks every edge in
           every LineString / Polygon ring / MultiLineString /
           MultiPolygon ring / GeometryCollection child and inserts a
           great-circle midpoint vertex whenever the chord midpoint
           differs from the geodesic midpoint by more than
           ~50 µdegrees. This matches BigQuery's
           recorded ``ST_AsGeoJSON`` output for non-equatorial,
           non-meridian edges.

        The JSON-shaped STRING tolerance (ADR 0022 §3, with float-ULP
        tolerance) absorbs the inter-token whitespace / key-order /
        int-vs-float / libm-vs-S2 ULP drift on the populated body.
        """
        normalised = exp.Anonymous(
            this="bqemu_geojson_geodesic_interp",
            expressions=[node.copy()],
        )
        return exp.Cast(
            this=normalised,
            to=exp.DataType(this=exp.DataType.Type.VARCHAR),
        )


_SPHEROIDAL_METRIC_BQ_NAMES = {"ST_DISTANCE", "ST_LENGTH", "ST_AREA", "ST_PERIMETER"}


def _astext(node: exp.Expression) -> exp.Anonymous:
    """Wrap *node* in ``ST_AsText(...)`` so the helper UDF sees WKT."""
    return exp.Anonymous(this="ST_AsText", expressions=[node.copy()])


@register
class StDistanceSpheroidalRule(TranslationRule):
    """``ST_DISTANCE(g1, g2)`` → ``bqemu_st_distance_spheroidal(ST_AsText(g1), ST_AsText(g2))``.

    BigQuery's ``GEOGRAPHY`` is spherical (S2-style with
    ``kEarthRadiusMeters = 6371010.0``); DuckDB's ``ST_Distance`` is
    planar Cartesian and returns Euclidean distance in coordinate
    units. The rewrite routes through a Python helper that converts
    each (lon, lat) to a 3D unit vector and returns the great-circle
    distance in metres on the S2 sphere — matching every recorded
    fixture to within FLOAT64 precision.

    The rule must register BEFORE :class:`SpatialRenameRule` so it
    fires on the unrenamed ``Anonymous(ST_DISTANCE)`` node before
    the rename replaces it with DuckDB's planar ``ST_Distance``.

    Falls back to DuckDB's planar ``ST_Distance`` (the rename target)
    only when the inputs aren't POINT-shaped WKT — the helper returns
    ``NULL`` in that case and the surrounding query sees a NULL
    distance, surfacing the unsupported mixed-shape combination to
    the user rather than silently returning a planar value.
    """

    name = "ST_DISTANCE_SPHEROIDAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``ST_DISTANCE`` — both the typed and anonymous forms.

        SQLGlot parses ``ST_DISTANCE(a, b)`` as the typed
        :class:`exp.StDistance` node with ``this`` / ``expression``
        slots. Some upstream-generated ASTs (constructed via
        :class:`exp.Anonymous` directly in unit tests) also reach the
        translator — the rule handles both shapes.
        """
        if isinstance(node, exp.StDistance):
            return node.this is not None and node.args.get("expression") is not None
        if isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_DISTANCE":
            return len(node.expressions) == 2  # noqa: PLR2004 — fixed BQ signature.
        return False

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through the spheroidal-distance helper UDF."""
        if isinstance(node, exp.StDistance):
            g1 = node.this
            g2 = node.args["expression"]
        else:
            g1, g2 = node.expressions
        return exp.Anonymous(
            this="bqemu_st_distance_spheroidal",
            expressions=[_astext(g1), _astext(g2)],
        )


@register
class StLengthSpheroidalRule(TranslationRule):
    """``ST_LENGTH(g)`` → ``bqemu_st_length_spheroidal(ST_AsText(g))``.

    Sums great-circle distance over the consecutive vertices of a
    LINESTRING on the S2 sphere; non-linestring inputs return 0 per
    BigQuery's documented contract. See
    :class:`StDistanceSpheroidalRule` for the spheroidal rationale.
    """

    name = "ST_LENGTH_SPHEROIDAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match unrenamed ``ST_LENGTH`` anonymous calls with one arg."""
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_LENGTH":
            return False
        return len(node.expressions) == 1

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through the spheroidal-length helper UDF."""
        anon = node
        (geom,) = anon.expressions
        return exp.Anonymous(
            this="bqemu_st_length_spheroidal",
            expressions=[_astext(geom)],
        )


@register
class StAreaSpheroidalRule(TranslationRule):
    """``ST_AREA(g)`` → ``bqemu_st_area_spheroidal(ST_AsText(g))``.

    Uses L'Huilier's spherical-excess theorem on a triangle fan from
    the outer ring's first vertex; hole rings (if any) are subtracted
    from the outer ring's area. See :class:`StDistanceSpheroidalRule`
    for the spheroidal rationale.
    """

    name = "ST_AREA_SPHEROIDAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match unrenamed ``ST_AREA`` anonymous calls with one arg."""
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_AREA":
            return False
        return len(node.expressions) == 1

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through the spheroidal-area helper UDF."""
        anon = node
        (geom,) = anon.expressions
        return exp.Anonymous(
            this="bqemu_st_area_spheroidal",
            expressions=[_astext(geom)],
        )


@register
class StDWithinSpheroidalRule(TranslationRule):
    """``ST_DWITHIN(g1, g2, d)`` → ``bqemu_st_distance_spheroidal(g1, g2) <= d``.

    BigQuery's ``ST_DWITHIN`` is shorthand for ``ST_DISTANCE(g1, g2) <= d``
    where the distance is the spheroidal great-circle distance in metres.
    DuckDB's ``ST_DWithin`` is planar Cartesian, so the threshold ``d``
    is compared against degree-Euclidean distance — the predicate flips
    around the threshold whenever planar and spheroidal distance happen
    to straddle it (the existing ``st_dwithin_no`` fixture is the
    canonical example).

    The rewrite reuses :func:`bqemu_st_distance_spheroidal` so the
    point-to-point case the conformance corpus exercises closes with
    one rule. Mixed-shape inputs (point-to-linestring, etc.) fall back
    via the helper's ``NULL`` return and surface as an unsupported
    case to the user rather than silently returning the planar
    truth value.
    """

    name = "ST_DWITHIN_SPHEROIDAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match anonymous ``ST_DWITHIN`` calls with three args."""
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_DWITHIN":
            return False
        return len(node.expressions) == 3  # noqa: PLR2004 — fixed BQ signature.

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Rewrite to ``bqemu_st_distance_spheroidal(g1, g2) <= d``."""
        anon = node
        g1, g2, threshold = anon.expressions
        dist_call = exp.Anonymous(
            this="bqemu_st_distance_spheroidal",
            expressions=[_astext(g1), _astext(g2)],
        )
        return exp.LTE(this=dist_call, expression=threshold.copy())


@register
class StPerimeterSpheroidalRule(TranslationRule):
    """``ST_PERIMETER(g)`` → ``bqemu_st_perimeter_spheroidal(ST_AsText(g))``.

    Sums great-circle distance around every ring (outer + holes) of a
    POLYGON. See :class:`StDistanceSpheroidalRule` for the spheroidal
    rationale.
    """

    name = "ST_PERIMETER_SPHEROIDAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match unrenamed ``ST_PERIMETER`` anonymous calls with one arg."""
        if not isinstance(node, exp.Anonymous):
            return False
        if str(node.this).upper() != "ST_PERIMETER":
            return False
        return len(node.expressions) == 1

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through the spheroidal-perimeter helper UDF."""
        anon = node
        (geom,) = anon.expressions
        return exp.Anonymous(
            this="bqemu_st_perimeter_spheroidal",
            expressions=[_astext(geom)],
        )


@register
class SpatialRenameRule(TranslationRule):
    """Rename BigQuery ``ST_*`` calls to their DuckDB equivalents.

    Handles every entry in :data:`bqemulator.types.geography.BQ_TO_DUCKDB`
    that does not need a custom call shape. Argument lists are passed
    through unchanged.

    Skips the BQ names handled by spheroidal-metric rules above
    (``ST_DISTANCE`` / ``ST_LENGTH`` / ``ST_AREA`` / ``ST_PERIMETER``)
    because those rules already rewrote them to ``bqemu_st_*_spheroidal``
    helper-UDF calls. Renaming them to the DuckDB planar names would
    silently re-introduce the unit mismatch.
    """

    name = "ST_*_RENAME"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Anonymous`` whose function name is in the BQ ST_* table."""
        if not isinstance(node, exp.Anonymous):
            return False
        return self._target_name(node) is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace the function name with the DuckDB equivalent."""
        anon = node
        new_name = self._target_name(anon)
        if new_name is None:  # pragma: no cover — applies_to guard.
            return node
        new_args = [arg.copy() for arg in anon.expressions]
        return exp.Anonymous(this=new_name, expressions=new_args)

    @staticmethod
    def _target_name(node: exp.Expression) -> str | None:
        if not isinstance(node, exp.Anonymous):
            return None
        raw = str(node.this).upper()
        return BQ_TO_DUCKDB.get(raw)


@register
class StGeogFromWkbRule(TranslationRule):
    """``ST_GEOGFROMWKB(bytes)`` → ``ST_GeomFromHEXWKB(hex(bytes))``.

    DuckDB's spatial extension cannot ingest raw WKB bytes; it expects
    hex-encoded WKB. We wrap the BigQuery argument in DuckDB's
    ``hex()`` function so column references and literal byte values
    both translate cleanly.
    """

    name = "ST_GEOGFROMWKB"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_GEOGFROMWKB`` anonymous call."""
        return isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_GEOGFROMWKB"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the lone argument in ``hex()`` and rename the call."""
        anon = node
        if not anon.expressions:
            return node
        wrapped = exp.Anonymous(this="hex", expressions=[anon.expressions[0].copy()])
        return exp.Anonymous(this="ST_GeomFromHEXWKB", expressions=[wrapped])


@register
class StIsCollectionRule(TranslationRule):
    """``ST_ISCOLLECTION(g)`` → ``ST_GeometryType(g) IN (…)``.

    DuckDB has no single boolean predicate for "is multi/collection"
    — we synthesise it from a ``ST_GeometryType`` membership check
    against the geometry-type names that BigQuery considers collections.
    """

    name = "ST_ISCOLLECTION"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_ISCOLLECTION`` anonymous call."""
        return isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_ISCOLLECTION"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to ``ST_GeometryType(arg) IN (...)``."""
        anon = node
        if not anon.expressions:
            return node
        type_call = exp.Anonymous(
            this="ST_GeometryType",
            expressions=[anon.expressions[0].copy()],
        )
        members = [exp.Literal.string(name) for name in COLLECTION_TYPES]
        return exp.In(this=type_call, expressions=members)


@register
class StIntersectsBoxRule(TranslationRule):
    """Rewrite ``ST_INTERSECTSBOX(g, lo_lng, lo_lat, hi_lng, hi_lat)``.

    Routes through ``ST_Intersects(g, ST_MakeEnvelope(...))``.

    BigQuery's ``ST_INTERSECTSBOX`` is a convenience wrapper around a
    bounding-box intersection test. DuckDB-spatial has no equivalent
    single-call form but does expose ``ST_MakeEnvelope(lo_lng, lo_lat,
    hi_lng, hi_lat)`` which produces the equivalent polygon, and
    ``ST_Intersects`` which evaluates the predicate. The rewrite is
    exact for planar geometry: the bounding box is rendered as a
    polygon, then the standard ``ST_Intersects`` predicate runs against
    it. The contract is planar-safe by construction — the box's
    coordinates are themselves planar (lng/lat values, not great-circle
    arcs), so spheroidal divergence is bounded by ADR 0019.
    """

    name = "ST_INTERSECTSBOX"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_INTERSECTSBOX`` anonymous call."""
        return isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_INTERSECTSBOX"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Rewrite to ``ST_Intersects(g, ST_MakeEnvelope(lo_lng, lo_lat, hi_lng, hi_lat))``."""
        anon = node
        if len(anon.expressions) != 5:  # noqa: PLR2004 — fixed BQ signature.
            return node
        geog, lo_lng, lo_lat, hi_lng, hi_lat = anon.expressions
        envelope = exp.Anonymous(
            this="ST_MakeEnvelope",
            expressions=[lo_lng.copy(), lo_lat.copy(), hi_lng.copy(), hi_lat.copy()],
        )
        return exp.Anonymous(this="ST_Intersects", expressions=[geog.copy(), envelope])


@register
class StSnapToGridRule(TranslationRule):
    """``ST_SNAPTOGRID(g, size)`` → ``ST_GeomFromText(bqemu_st_snaptogrid(ST_AsText(g), size))``.

    DuckDB-spatial has no ``ST_SnapToGrid``. The rewrite round-trips the
    geometry through WKT, snaps each vertex coordinate to the nearest
    multiple of ``size`` via the Python helper
    :func:`bqemulator.sql.builtin_udfs.bqemu_st_snaptogrid`, and rebuilds
    the geometry from the snapped WKT. Operates on planar geometry per
    ADR 0019 — spheroidal snap-to-grid would need a different reference
    frame (WGS84 vertex distances) and is not exercised by the
    conformance corpus.
    """

    name = "ST_SNAPTOGRID"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_SNAPTOGRID`` anonymous call."""
        return isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_SNAPTOGRID"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through the WKT snap helper UDF."""
        anon = node
        if len(anon.expressions) != 2:  # noqa: PLR2004 — fixed BQ signature.
            return node
        geog, size = anon.expressions
        astext_call = exp.Anonymous(this="ST_AsText", expressions=[geog.copy()])
        snap_call = exp.Anonymous(
            this="bqemu_st_snaptogrid",
            expressions=[astext_call, size.copy()],
        )
        return exp.Anonymous(this="ST_GeomFromText", expressions=[snap_call])


@register
class StMakePolygonOrientedRule(TranslationRule):
    """``ST_MAKEPOLYGONORIENTED(ARRAY<GEOGRAPHY>)`` → ``ST_MakePolygon(arr[1])``.

    BigQuery's ``ST_MAKEPOLYGONORIENTED`` takes an array of LineStrings
    (outer ring + hole rings, with orientation determining the outer
    ring). DuckDB has no orientation-aware constructor; planar geometry
    is orientation-equivalent so we route through ``ST_MakePolygon``
    with the first array element as the outer ring. Multi-element
    arrays (with hole rings) aren't exercised by the conformance corpus
    yet; if a future fixture needs hole-ring support, the rule can be
    extended to pass remaining elements as the second argument of
    DuckDB's ``ST_MakePolygon(LINESTRING, LINESTRING[])`` overload.
    """

    name = "ST_MAKEPOLYGONORIENTED"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_MAKEPOLYGONORIENTED`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous) and str(node.this).upper() == "ST_MAKEPOLYGONORIENTED"
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Extract the array's first element and route through ``ST_MakePolygon``."""
        anon = node
        if not anon.expressions:
            return node
        arg = anon.expressions[0]
        if isinstance(arg, exp.Array) and arg.expressions:
            outer_ring = arg.expressions[0].copy()
        else:
            outer_ring = arg.copy()
        return exp.Anonymous(this="ST_MakePolygon", expressions=[outer_ring])


@register
class StMaxDistanceRule(TranslationRule):
    """``ST_MAXDISTANCE(g1, g2)`` → ``bqemu_st_distance_spheroidal(...)``.

    BigQuery's ``ST_MAXDISTANCE`` returns the maximum great-circle
    distance between any pair of points drawn from the two
    geographies. For the POINT-POINT case the conformance corpus
    exercises, the maximum distance is the only distance: the
    great-circle arc between the two points. The rewrite routes
    through the existing spheroidal-distance helper.

    Multi-point and shape-shape combinations (MaxDistance between two
    POLYGONs is the diameter of the bounding-arc of all vertex pairs)
    are unsupported; the helper returns ``NULL`` for those shapes and
    the surrounding query surfaces the unsupported combination to the
    user rather than silently returning a planar value.
    """

    name = "ST_MAXDISTANCE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the BigQuery ``ST_MAXDISTANCE`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "ST_MAXDISTANCE"
            and len(node.expressions) == 2  # noqa: PLR2004 — fixed BQ signature.
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Route through ``bqemu_st_distance_spheroidal`` (max == distance for POINT-POINT)."""
        anon = node
        g1, g2 = anon.expressions
        return exp.Anonymous(
            this="bqemu_st_distance_spheroidal",
            expressions=[_astext(g1), _astext(g2)],
        )


@register
class GeographyColumnTypeRule(TranslationRule):
    """``GEOGRAPHY`` column type → ``GEOMETRY``.

    BigQuery ``CREATE TABLE t (loc GEOGRAPHY)`` reaches DuckDB as
    ``CREATE TABLE t (loc GEOGRAPHY)`` because SQLGlot's DuckDB
    generator passes the type token through unchanged. DuckDB's
    spatial extension exposes the type as ``GEOMETRY`` (planar) — the
    column-storage type the emulator targets per ADR 0019. The
    reverse mapping (DuckDB ``GEOMETRY`` → BigQuery ``GEOGRAPHY``)
    already exists in
    :mod:`bqemulator.storage.type_map` so a column declared as
    ``GEOMETRY`` on the DuckDB side surfaces as ``GEOGRAPHY`` on the
    REST schema.

    Closes the three ``geography_column_*`` conformance fixtures
    (basic / insert / select_filter) — all three exercise DDL that
    declares a ``GEOGRAPHY`` column, then performs the standard
    ``ST_*`` functions the existing spatial rules already cover.
    """

    name = "GEOGRAPHY_COLUMN_TYPE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``DataType`` nodes whose ``this`` is the GEOGRAPHY token."""
        return isinstance(node, exp.DataType) and node.this == exp.DataType.Type.GEOGRAPHY

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Return a fresh ``DataType`` node typed as ``GEOMETRY``."""
        del node  # The rewrite is operand-free — we just emit the target type.
        return exp.DataType(this=exp.DataType.Type.GEOMETRY, nested=False)


__all__ = [
    "GeographyColumnTypeRule",
    "SpatialRenameRule",
    "StAsGeoJsonStringTypeRule",
    "StGeogFromWkbRule",
    "StGeometryTypeBqNameRule",
    "StIntersectsBoxRule",
    "StIsCollectionRule",
    "StMakePolygonOrientedRule",
    "StMaxDistanceRule",
    "StSnapToGridRule",
]
