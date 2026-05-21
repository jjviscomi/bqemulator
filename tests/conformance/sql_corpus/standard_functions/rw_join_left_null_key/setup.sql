CREATE OR REPLACE TABLE `${DATASET}.left_t` (id INT64, label STRING);
INSERT INTO `${DATASET}.left_t` (id, label) VALUES (1, "a"), (CAST(NULL AS INT64), "b"), (3, "c");
CREATE OR REPLACE TABLE `${DATASET}.right_t` (id INT64, descr STRING);
INSERT INTO `${DATASET}.right_t` (id, descr) VALUES (1, "alpha"), (CAST(NULL AS INT64), "ignored"), (3, "gamma");
