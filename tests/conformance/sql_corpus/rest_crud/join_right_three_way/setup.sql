CREATE OR REPLACE TABLE `${DATASET}.t_a` (id INT64, val STRING);
INSERT INTO `${DATASET}.t_a` (id, val) VALUES (1, 'a1'), (2, 'a2');

CREATE OR REPLACE TABLE `${DATASET}.t_b` (id INT64, val STRING);
INSERT INTO `${DATASET}.t_b` (id, val) VALUES (2, 'b2'), (3, 'b3');

CREATE OR REPLACE TABLE `${DATASET}.t_c` (id INT64, val STRING);
INSERT INTO `${DATASET}.t_c` (id, val) VALUES (1, 'c1'), (2, 'c2'), (3, 'c3'), (4, 'c4');
