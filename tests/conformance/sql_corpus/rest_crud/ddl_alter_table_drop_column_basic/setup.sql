CREATE OR REPLACE TABLE `${DATASET}.t_drop_col` (
  id INT64,
  name STRING,
  scratch STRING
);
INSERT INTO `${DATASET}.t_drop_col` (id, name, scratch) VALUES
  (1, 'a', 'x'),
  (2, 'b', 'y');
ALTER TABLE `${DATASET}.t_drop_col` DROP COLUMN scratch;
