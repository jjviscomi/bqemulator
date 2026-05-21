-- City-scale linestring length: ~10 km north-south at NYC.
SELECT ST_LENGTH(ST_GEOGFROMTEXT(
  'LINESTRING(-73.9857 40.7580, -73.9857 40.8480)'
)) AS meters
