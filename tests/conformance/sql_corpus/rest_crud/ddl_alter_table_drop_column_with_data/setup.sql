CREATE OR REPLACE TABLE `${DATASET}.t_drop_data` (
  id INT64,
  payload STRING,
  obsolete_col INT64
);
INSERT INTO `${DATASET}.t_drop_data` (id, payload, obsolete_col) VALUES
  (1, 'keep-this', 999),
  (2, 'also-keep', 888),
  (3, 'final-row', 777);
ALTER TABLE `${DATASET}.t_drop_data` DROP COLUMN obsolete_col;
