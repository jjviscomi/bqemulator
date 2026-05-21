CREATE OR REPLACE TABLE `${DATASET}.t_pre_rename` (id INT64, val STRING);
INSERT INTO `${DATASET}.t_pre_rename` (id, val) VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma');
ALTER TABLE `${DATASET}.t_pre_rename` RENAME TO t_post_rename;
