SELECT name, ST_ASTEXT(loc) AS wkt FROM `${DATASET}.geo_places` ORDER BY name
