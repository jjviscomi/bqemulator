CREATE OR REPLACE TABLE `${DATASET}.t_old_name` (id INT64);
INSERT INTO `${DATASET}.t_old_name` (id) VALUES (1), (2);
ALTER TABLE `${DATASET}.t_old_name` RENAME TO t_new_name;
