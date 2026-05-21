-- Street-scale buffer: 10 m radius at the equator.
SELECT ST_ASTEXT(
  ST_BUFFER(ST_GEOGPOINT(0, 0), 10)
) AS wkt
