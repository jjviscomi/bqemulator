SELECT segment_id, ST_NUMPOINTS(shape) AS pts
FROM `${DATASET}.geo_lines`
ORDER BY segment_id
