-- State-scale linestring length: SF → LA (~559 km).
SELECT ST_LENGTH(ST_GEOGFROMTEXT(
  'LINESTRING(-122.4194 37.7749, -118.2437 34.0522)'
)) AS meters
