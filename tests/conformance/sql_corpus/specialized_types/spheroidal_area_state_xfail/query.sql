-- State-scale area: Wyoming-shaped polygon (~9° × ~4° ≈ 253000 km²).
SELECT ST_AREA(ST_GEOGFROMTEXT(
  'POLYGON((-111 41, -104 41, -104 45, -111 45, -111 41))'
)) AS sq_meters
