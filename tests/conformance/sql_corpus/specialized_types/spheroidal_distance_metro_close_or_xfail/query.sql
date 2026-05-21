-- Metropolitan-scale distance: NYC ↔ Trenton (~85 km).
SELECT ST_DISTANCE(
  ST_GEOGPOINT(-74.0060, 40.7128),
  ST_GEOGPOINT(-74.7430, 40.2206)
) AS meters
