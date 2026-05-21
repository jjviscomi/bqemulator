CREATE OR REPLACE TABLE `${DATASET}.avro_nested` (
  id INT64,
  point STRUCT<x INT64, y INT64>,
  tags ARRAY<STRING>
);

INSERT INTO `${DATASET}.avro_nested` VALUES
  (1, STRUCT(10, 20), ['a', 'b']),
  (2, STRUCT(30, 40), ['c']);
