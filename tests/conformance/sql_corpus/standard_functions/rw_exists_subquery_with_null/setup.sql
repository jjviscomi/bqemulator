CREATE OR REPLACE TABLE `${DATASET}.with_nulls` (id INT64, v STRING);
INSERT INTO `${DATASET}.with_nulls` (id, v) VALUES (1, "a"), (2, NULL), (3, NULL);
