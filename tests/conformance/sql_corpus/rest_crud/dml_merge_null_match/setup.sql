CREATE OR REPLACE TABLE `${DATASET}.target` (id INT64, v STRING);
INSERT INTO `${DATASET}.target` (id, v) VALUES (1, "a"), (CAST(NULL AS INT64), "b"), (3, "c");
CREATE OR REPLACE TABLE `${DATASET}.source` (id INT64, v STRING);
INSERT INTO `${DATASET}.source` (id, v) VALUES (1, "A"), (CAST(NULL AS INT64), "B"), (4, "D");
