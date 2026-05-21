CREATE OR REPLACE TABLE `${DATASET}.arrays` (id INT64, arr ARRAY<INT64>);
INSERT INTO `${DATASET}.arrays` (id, arr) VALUES
  (1, [10, 20]),
  (2, NULL),
  (3, [30, 40]),
  (4, NULL),
  (5, [50]);
