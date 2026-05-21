-- National-scale distance: NYC ↔ Denver (~2620 km).
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-74.0060, 40.7128),
  ST_GEOGPOINT(-104.9903, 39.7392)
) AS meters
