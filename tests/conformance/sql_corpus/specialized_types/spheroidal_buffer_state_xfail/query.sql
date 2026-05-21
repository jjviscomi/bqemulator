-- State-scale buffer: 100 km radius at the equator.
SELECT ST_ASTEXT(
  ST_BUFFER(ST_GEOGPOINT(0, 0), 100000)
) AS wkt
