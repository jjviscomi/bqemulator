-- City-scale area: a ~10 km × ~10 km polygon around NYC.
-- 0.1° × 0.1° at lat 40.75 ≈ 11.1 km × 8.4 km ≈ 93 km² ≈ 9.3e7 m².
SELECT ST_AREA(ST_GEOGFROMTEXT(
  'POLYGON((-74.05 40.70, -73.95 40.70, -73.95 40.80, -74.05 40.80, -74.05 40.70))'
)) AS sq_meters
