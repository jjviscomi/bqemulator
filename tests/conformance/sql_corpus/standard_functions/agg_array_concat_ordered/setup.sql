CREATE OR REPLACE TABLE `${DATASET}.arrays` (id INT64, arr ARRAY<STRING>);
INSERT INTO `${DATASET}.arrays` (id, arr) VALUES
  (2, ["b1", "b2"]),
  (3, ["c1"]),
  (1, ["a1", "a2", "a3"]);
