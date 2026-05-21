CREATE OR REPLACE TABLE `${DATASET}.geo_named` (
  id INT64,
  loc GEOGRAPHY
);
INSERT INTO `${DATASET}.geo_named` (id, loc) VALUES
  (1, ST_GEOGPOINT(0, 0)),
  (2, ST_GEOGPOINT(10, 10)),
  (3, NULL);
