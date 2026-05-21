CREATE OR REPLACE TABLE `${DATASET}.base_with_nulls` (group_id INT64, amount NUMERIC);
INSERT INTO `${DATASET}.base_with_nulls` (group_id, amount) VALUES
  (1, NUMERIC "10.00"), (1, NULL), (2, NULL), (2, NULL), (3, NUMERIC "30.00");
