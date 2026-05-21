CREATE OR REPLACE TABLE `${DATASET}.left_t2` (id INT64, label STRING);
INSERT INTO `${DATASET}.left_t2` (id, label) VALUES (1, "a"), (2, "b"), (3, "c");
CREATE OR REPLACE TABLE `${DATASET}.right_t2` (id INT64, descr STRING);
