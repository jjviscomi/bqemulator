# GEOGRAPHY and spatial

Status: shipped.

The emulator implements BigQuery's `GEOGRAPHY` type by backing it with
DuckDB's `spatial` extension (`GEOMETRY`). The extension is **required**:
the emulator fails fast at startup if it can't be loaded — see
[ADR 0019](../adr/0019-specialized-types.md) for the rationale.

## Quick example

```python
from google.cloud import bigquery

client = bigquery.Client(...)  # pointing at bqemulator

# 1. Create a table with a GEOGRAPHY column.
schema = [
    bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("loc", "GEOGRAPHY"),
]
client.create_table(bigquery.Table("p.ds.places", schema=schema))

# 2. Insert WKT strings — the emulator converts them to WKB and stores
#    them in DuckDB's GEOMETRY column.
client.insert_rows_json(
    "p.ds.places",
    [
        {"id": 1, "loc": "POINT(-73.985 40.758)"},  # Times Square
        {"id": 2, "loc": "POINT(-73.975 40.785)"},  # Central Park
        {"id": 3, "loc": "POINT(-122.41 37.77)"},   # San Francisco
    ],
)

# 3. Spatial query: which points are within 5km of Times Square?
job = client.query(
    "SELECT id FROM `p.ds.places` "
    "WHERE ST_DWITHIN(loc, ST_GEOGFROMTEXT('POINT(-73.985 40.758)'), 5000) "
    "ORDER BY id",
)
for row in job.result():
    print(row.id)
```

## Constructors

| BigQuery | DuckDB equivalent | Notes |
|------------------------|-----------------------------|--------------------------------------|
| `ST_GEOGFROMTEXT(wkt)` | `ST_GeomFromText(wkt)` | WKT input |
| `ST_GEOGFROMGEOJSON(j)`| `ST_GeomFromGeoJSON(j)` | GeoJSON input |
| `ST_GEOGFROMWKB(b)` | `ST_GeomFromHEXWKB(hex(b))` | Raw WKB bytes → hex inside DuckDB |
| `ST_GEOGPOINT(lon, lat)`| `ST_Point(lon, lat)` | (longitude, latitude) order |

## Predicates and measurements

`ST_DWITHIN`, `ST_INTERSECTS`, `ST_CONTAINS`, `ST_WITHIN`, `ST_DISTANCE`,
`ST_AREA`, `ST_PERIMETER`, `ST_LENGTH`, `ST_X`, `ST_Y`, `ST_GEOMETRYTYPE`,
`ST_DIMENSION`, `ST_ISEMPTY`, `ST_NUMPOINTS` / `ST_NPOINTS`,
`ST_BOUNDINGBOX` (aliased to `ST_Envelope`), `ST_ISCOLLECTION` all map
to their DuckDB counterparts (or, for the renamed/derived ones, to
equivalent expressions). See
[reference/sql-function-mapping.md](../reference/sql-function-mapping.md)
for the complete table.

## Set operations

`ST_UNION`, `ST_INTERSECTION`, `ST_BUFFER`, `ST_CENTROID`, `ST_CONVEXHULL`,
`ST_DUMP` round-trip through DuckDB unchanged.

## Output

`ST_ASTEXT(g)` returns the canonical WKT representation; `ST_ASGEOJSON(g)`
returns GeoJSON. When a `GEOGRAPHY` column is projected directly in a
result set, the emulator converts the stored WKB to WKT on the way to
the REST wire format — clients see strings like `"POINT (1 2)"`.

## Limitations

* DuckDB's `GEOMETRY` is planar (Cartesian); BigQuery's `GEOGRAPHY` is
  spheroidal. Distance / area / perimeter values agree at small scales
  but diverge at continental scales. Use the emulator for unit tests
  and CI flow validation; rely on real BigQuery for production
  geo-analytics accuracy. ADR 0019 records the trade-off.
* `ST_GeogFromWKB` requires the WKB input bytes to be valid; an invalid
  payload surfaces as a DuckDB error.

## See also

* [ADR 0019 — Specialized types](../adr/0019-specialized-types.md)
* [Architecture: specialized types](../architecture/specialized-types.md)
* [SQL function mapping](../reference/sql-function-mapping.md)
