CREATE OR REPLACE TABLE `${DATASET}.avro_projection` (
  a INT64,
  b STRING,
  c STRING,
  d FLOAT64
);

INSERT INTO `${DATASET}.avro_projection` VALUES
  (1, 'one', 'x', 1.1),
  (2, 'two', 'y', 2.2),
  (3, 'three', 'z', 3.3);
