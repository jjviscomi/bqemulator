-- High-latitude distance: 10° of longitude at lat 80°N.
-- At lat 80° the meridian shrinks via cos(80°)≈0.1736, so 10° lng ≈ 193 km
-- spheroidal. The planar Euclidean delta is 10 degrees (units-only),
-- exposing the divergence ~3× faster than at the equator.
SELECT ST_DISTANCE(
  ST_GEOGPOINT(0, 80),
  ST_GEOGPOINT(10, 80)
) AS meters
