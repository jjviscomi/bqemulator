-- State-scale distance: San Francisco ↔ Los Angeles (~559 km).
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-122.4194, 37.7749),
  ST_GEOGPOINT(-118.2437, 34.0522)
) AS meters
