-- Neighborhood-scale area: a ~1 km × ~1 km polygon in NYC.
-- 0.01° × 0.01° at lat 40.75 ≈ 1.11 km × 0.84 km ≈ 0.94 km² ≈ 940000 m².
SELECT ST_AREA(ST_GEOGFROMTEXT(
  'POLYGON((-73.99 40.75, -73.98 40.75, -73.98 40.76, -73.99 40.76, -73.99 40.75))'
)) AS sq_meters
