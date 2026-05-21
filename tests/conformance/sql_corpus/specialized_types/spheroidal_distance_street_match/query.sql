-- Street-scale distance: two points ~100 m apart at NYC latitude.
-- 0.0011 deg longitude at lat 40.758 ≈ 0.0011 * cos(40.758°) * 111320 m ≈ 93 m.
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-73.9855, 40.7580),
  ST_GEOGPOINT(-73.9844, 40.7580)
) AS meters
