CREATE OR REPLACE TABLE `${DATASET}.geo_lines` (
  segment_id INT64,
  shape GEOGRAPHY
);
INSERT INTO `${DATASET}.geo_lines` (segment_id, shape) VALUES
  (1, ST_GEOGFROMTEXT('LINESTRING(0 0, 1 1, 2 2)')),
  (2, ST_GEOGFROMTEXT('LINESTRING(10 10, 20 20)'));
