"""GEOGRAPHY codec and BigQuery → DuckDB function mapping.

BigQuery's ``GEOGRAPHY`` type is backed by DuckDB's ``GEOMETRY`` type
(provided by the ``spatial`` extension). The two are not 1:1:

* DuckDB's ``GEOMETRY`` is planar (Cartesian); BigQuery's ``GEOGRAPHY``
  is spheroidal. Function semantics for distance / area / perimeter
  differ at scale (millimeters vs. metres on the sphere). The emulator
  ships the planar DuckDB implementation as the *operational* substitute
  — ADR 0019 records this decision.
* DuckDB exposes ``ST_GeomFromText`` / ``ST_GeomFromGeoJSON`` /
  ``ST_GeomFromHEXWKB``; BigQuery exposes ``ST_GeogFromText`` /
  ``ST_GeogFromGeoJson`` / ``ST_GeogFromWkb``. Names and binary input
  format differ (BQ takes raw ``BYTES``, DuckDB takes hex-encoded WKB).

This module owns the mapping table and the helpers used by
``sql.rules.spatial``. Neither row data nor REST parameters touch this
module directly — geometry values flow through DuckDB itself.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import duckdb


# ---------------------------------------------------------------------------
# Function-name and signature mapping (BigQuery ↔ DuckDB)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SpatialFunctionMapping:
    """A single BigQuery → DuckDB spatial-function mapping.

    Attributes:
        bq_name: BigQuery function name (case-insensitive in the parser).
        duckdb_name: DuckDB function name.
        notes: Human-readable description of the mapping for the
            ``sql-function-mapping`` reference doc.
    """

    bq_name: str
    duckdb_name: str
    notes: str = ""


#: Direct one-for-one mappings — same arity, same arg order, same result
#: type. Translation rules emit an ``Anonymous(this=duckdb_name, ...)``.
DIRECT_MAPPINGS: tuple[SpatialFunctionMapping, ...] = (
    # Constructors
    SpatialFunctionMapping("ST_GEOGFROMTEXT", "ST_GeomFromText"),
    SpatialFunctionMapping("ST_GEOGFROMGEOJSON", "ST_GeomFromGeoJSON"),
    SpatialFunctionMapping("ST_GEOGPOINT", "ST_Point", notes="(longitude, latitude)"),
    SpatialFunctionMapping("ST_MAKELINE", "ST_MakeLine"),
    SpatialFunctionMapping("ST_MAKEPOLYGON", "ST_MakePolygon"),
    # Predicates / measurements
    SpatialFunctionMapping("ST_DWITHIN", "ST_DWithin"),
    SpatialFunctionMapping("ST_INTERSECTS", "ST_Intersects"),
    SpatialFunctionMapping("ST_CONTAINS", "ST_Contains"),
    SpatialFunctionMapping("ST_WITHIN", "ST_Within"),
    SpatialFunctionMapping("ST_DISJOINT", "ST_Disjoint"),
    SpatialFunctionMapping("ST_TOUCHES", "ST_Touches"),
    SpatialFunctionMapping("ST_COVERS", "ST_Covers"),
    SpatialFunctionMapping("ST_COVEREDBY", "ST_CoveredBy"),
    SpatialFunctionMapping("ST_ISCLOSED", "ST_IsClosed"),
    SpatialFunctionMapping("ST_ISRING", "ST_IsRing"),
    SpatialFunctionMapping("ST_DISTANCE", "ST_Distance"),
    SpatialFunctionMapping("ST_AREA", "ST_Area"),
    SpatialFunctionMapping("ST_PERIMETER", "ST_Perimeter"),
    SpatialFunctionMapping("ST_LENGTH", "ST_Length"),
    SpatialFunctionMapping("ST_X", "ST_X"),
    SpatialFunctionMapping("ST_Y", "ST_Y"),
    SpatialFunctionMapping("ST_POINTN", "ST_PointN", notes="1-indexed in both BQ and DuckDB"),
    SpatialFunctionMapping("ST_CLOSESTPOINT", "ST_ClosestPoint"),
    # Set ops
    SpatialFunctionMapping("ST_UNION", "ST_Union"),
    SpatialFunctionMapping("ST_UNION_AGG", "ST_Union_Agg", notes="Aggregate over GEOGRAPHY"),
    SpatialFunctionMapping("ST_INTERSECTION", "ST_Intersection"),
    SpatialFunctionMapping("ST_DIFFERENCE", "ST_Difference"),
    SpatialFunctionMapping("ST_BUFFER", "ST_Buffer"),
    SpatialFunctionMapping("ST_CENTROID", "ST_Centroid"),
    SpatialFunctionMapping("ST_CONVEXHULL", "ST_ConvexHull"),
    SpatialFunctionMapping("ST_BOUNDARY", "ST_Boundary"),
    SpatialFunctionMapping("ST_SIMPLIFY", "ST_Simplify"),
    # Output
    SpatialFunctionMapping("ST_ASTEXT", "ST_AsText"),
    SpatialFunctionMapping("ST_ASGEOJSON", "ST_AsGeoJSON"),
    SpatialFunctionMapping("ST_ASBINARY", "ST_AsBinary", notes="WKB BYTES output"),
    # Inspection
    SpatialFunctionMapping("ST_GEOMETRYTYPE", "ST_GeometryType"),
    SpatialFunctionMapping("ST_DIMENSION", "ST_Dimension"),
    SpatialFunctionMapping("ST_ISEMPTY", "ST_IsEmpty"),
    SpatialFunctionMapping("ST_DUMP", "ST_Dump"),
)

#: BigQuery functions that DuckDB does not expose under a matching name
#: but that are *renamed*. ``ST_NUMPOINTS`` and ``ST_NPOINTS`` both map
#: to DuckDB's ``ST_NPoints`` (DuckDB has no separate ST_NumPoints).
RENAME_MAPPINGS: tuple[SpatialFunctionMapping, ...] = (
    SpatialFunctionMapping("ST_NPOINTS", "ST_NPoints"),
    SpatialFunctionMapping("ST_NUMPOINTS", "ST_NPoints"),
    SpatialFunctionMapping(
        "ST_BOUNDINGBOX",
        "ST_Envelope",
        notes="DuckDB has no separate boundingbox",
    ),
)

#: Index used by translation rules.
BQ_TO_DUCKDB: dict[str, str] = {
    m.bq_name: m.duckdb_name for m in (*DIRECT_MAPPINGS, *RENAME_MAPPINGS)
}


# ---------------------------------------------------------------------------
# WKB / WKT helpers
# ---------------------------------------------------------------------------


def wkb_bytes_to_hex(wkb: bytes) -> str:
    """Convert a raw WKB byte string to the uppercase hex form DuckDB takes.

    BigQuery's ``ST_GeogFromWkb`` accepts raw ``BYTES``; DuckDB's
    ``ST_GeomFromHEXWKB`` accepts the hex-encoded form. The translator
    rewrites the BigQuery call into ``ST_GeomFromHEXWKB(hex_literal)``,
    pre-encoding any literal byte argument here.

    Args:
        wkb: The raw WKB bytes.

    Returns:
        The uppercase hex string (no ``0x`` prefix).
    """
    return binascii.hexlify(wkb).decode("ascii").upper()


def wkb_hex_to_bytes(hex_str: str) -> bytes:
    """Convert a hex-encoded WKB string to raw bytes (inverse of :func:`wkb_bytes_to_hex`)."""
    cleaned = hex_str[2:] if hex_str.lower().startswith("0x") else hex_str
    return binascii.unhexlify(cleaned)


# ---------------------------------------------------------------------------
# WKB → WKT converter (used by Arrow → REST output for GEOMETRY columns).
# ---------------------------------------------------------------------------


_CONV_LOCK = threading.Lock()
_CONV_CONN: duckdb.DuckDBPyConnection | None = None


def wkb_to_wkt(wkb: bytes) -> str:
    """Convert a raw WKB byte string to its canonical WKT representation.

    Used by the Arrow → BigQuery REST row formatter when a GEOGRAPHY
    column is part of the result. DuckDB stores GEOMETRY values as WKB
    when shipped through Arrow (``geoarrow.wkb`` extension); BigQuery
    REST emits WKT strings (``"POINT(1 2)"``). This helper bridges the
    two using a lazily-initialised in-process DuckDB connection with
    the spatial extension loaded — keeping the conversion accurate
    without pulling in shapely or another heavyweight dependency.

    Thread-safe: the conversion connection is lazily constructed under
    a lock and reused across calls.
    """
    if not isinstance(wkb, (bytes, bytearray)):
        raise TypeError(
            f"wkb_to_wkt expected bytes, got {type(wkb).__name__}",
        )
    conn = _ensure_conv_conn()
    hex_form = binascii.hexlify(bytes(wkb)).decode("ascii")
    row = conn.execute(
        "SELECT ST_AsText(ST_GeomFromHEXWKB(?))",
        [hex_form],
    ).fetchone()
    if row is None or row[0] is None:
        return ""
    return str(row[0])


def _ensure_conv_conn() -> duckdb.DuckDBPyConnection:
    """Return a singleton DuckDB connection with the spatial extension loaded."""
    global _CONV_CONN  # noqa: PLW0603 — module-singleton, guarded by lock.
    with _CONV_LOCK:
        if _CONV_CONN is not None:
            return _CONV_CONN
        import duckdb as _duckdb

        conn = _duckdb.connect(":memory:")
        conn.execute("INSTALL spatial")
        conn.execute("LOAD spatial")
        _CONV_CONN = conn
        return conn


# ---------------------------------------------------------------------------
# Extras handled by translation rules with custom shapes
# ---------------------------------------------------------------------------


#: BigQuery ``ST_ISCOLLECTION(g)`` returns true when the geometry is a
#: multi-part type. DuckDB has no single equivalent, so the rule emits
#: ``ST_GeometryType(g) IN (...)``. The names list is locked here so
#: tests and docs cite the same set.
COLLECTION_TYPES: tuple[str, ...] = (
    "GEOMETRYCOLLECTION",
    "MULTIPOINT",
    "MULTILINESTRING",
    "MULTIPOLYGON",
)


__all__ = [
    "BQ_TO_DUCKDB",
    "COLLECTION_TYPES",
    "DIRECT_MAPPINGS",
    "RENAME_MAPPINGS",
    "SpatialFunctionMapping",
    "wkb_bytes_to_hex",
    "wkb_hex_to_bytes",
]
