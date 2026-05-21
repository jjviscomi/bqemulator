CREATE OR REPLACE TABLE `${DATASET}.left_t` (id INT64, val STRING);
INSERT INTO `${DATASET}.left_t` (id, val) VALUES (1, 'a'), (2, 'b');

CREATE OR REPLACE TABLE `${DATASET}.right_t` (id INT64, tag STRING);
INSERT INTO `${DATASET}.right_t` (id, tag) VALUES (2, 'matched'), (3, 'unmatched');
