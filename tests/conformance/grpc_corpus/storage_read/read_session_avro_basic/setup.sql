CREATE OR REPLACE TABLE `${DATASET}.avro_basic` (
  id INT64,
  value STRING,
  category STRING
);

INSERT INTO `${DATASET}.avro_basic` VALUES
  (1, 'alpha', 'a'),
  (2, 'beta', 'b'),
  (3, 'gamma', 'a');
