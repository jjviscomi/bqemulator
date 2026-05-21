CREATE OR REPLACE TABLE `${DATASET}.arrays` (id INT64, arr ARRAY<INT64>);
INSERT INTO `${DATASET}.arrays` (id, arr) VALUES
  (1, [1, 2]),
  (2, [3, 4]),
  (3, [5, 6]);
