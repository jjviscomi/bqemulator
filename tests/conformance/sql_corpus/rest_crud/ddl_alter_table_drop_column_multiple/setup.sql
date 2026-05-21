CREATE OR REPLACE TABLE `${DATASET}.t_drop_multi` (
  id INT64,
  a STRING,
  b STRING,
  c STRING
);
INSERT INTO `${DATASET}.t_drop_multi` (id, a, b, c) VALUES
  (1, 'x', 'y', 'z'),
  (2, 'p', 'q', 'r');
ALTER TABLE `${DATASET}.t_drop_multi` DROP COLUMN a;
ALTER TABLE `${DATASET}.t_drop_multi` DROP COLUMN b;
