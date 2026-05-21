CREATE OR REPLACE TABLE `${DATASET}.t_with_null_col` (id INT64, keep_me STRING, drop_me STRING);
INSERT INTO `${DATASET}.t_with_null_col` (id, keep_me, drop_me) VALUES
  (1, "a", NULL), (2, "b", NULL), (3, "c", NULL);
