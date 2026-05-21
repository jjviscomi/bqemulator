-- Neighborhood-scale buffer: 100 m radius at the equator.
SELECT ST_ASTEXT(
  ST_BUFFER(ST_GEOGPOINT(0, 0), 100)
) AS wkt
