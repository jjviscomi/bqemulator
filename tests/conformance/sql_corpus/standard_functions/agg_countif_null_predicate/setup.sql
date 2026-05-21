CREATE OR REPLACE TABLE `${DATASET}.mixed` (id INT64, v INT64);
INSERT INTO `${DATASET}.mixed` (id, v) VALUES (1, 1), (2, NULL), (3, 0), (4, NULL), (5, 5);
