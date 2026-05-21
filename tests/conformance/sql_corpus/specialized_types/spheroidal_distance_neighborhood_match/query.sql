-- Neighborhood-scale distance: two points ~1 km apart at NYC latitude.
-- 0.009 deg latitude ≈ 1001 m.
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-73.9857, 40.7580),
  ST_GEOGPOINT(-73.9857, 40.7670)
) AS meters
