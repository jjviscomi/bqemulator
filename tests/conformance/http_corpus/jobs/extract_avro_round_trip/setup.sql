CREATE TABLE `${DATASET}.src` (id INT64, val STRING);
INSERT INTO `${DATASET}.src` (id, val) VALUES (10, 'x'), (20, 'y');
CREATE TABLE `${DATASET}.dst` (id INT64, val STRING);
