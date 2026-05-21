-- City-scale distance: two points ~10 km apart along NYC's north-south axis.
-- 0.09 deg latitude ≈ 10.0 km.
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-73.9857, 40.7580),
  ST_GEOGPOINT(-73.9857, 40.8480)
) AS meters
