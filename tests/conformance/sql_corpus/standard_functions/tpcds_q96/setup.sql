-- TPC-DS Q96 setup — 4-table join (store_sales ⋈ household_demographics ⋈
-- time_dim ⋈ store) with multiple WHERE predicates.
-- Spec params: t_hour=20, t_minute>=30, hd_dep_count=7, s_store_name='ese'.

CREATE OR REPLACE TABLE `${DATASET}.time_dim` (
  t_time_sk INT64, t_hour INT64, t_minute INT64
);
INSERT INTO `${DATASET}.time_dim` VALUES
  (2030, 20, 30),
  (2031, 20, 45),
  (2029, 20, 15),
  (2100, 21, 0),
  (1930, 19, 30);

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_dep_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, 7),
  (2, 7),
  (3, 4),
  (4, 0);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "ese"),
  (2, "other");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_time_sk INT64, ss_hdemo_sk INT64, ss_store_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- 20:30 + 20:45 (matches t_hour=20 t_minute>=30); hdemo 1/2 hd_dep_count=7; store=1 ese
  (2030, 1, 1),
  (2031, 2, 1),
  (2030, 1, 1),
  -- 20:30 wrong store
  (2030, 1, 2),
  -- 20:30 wrong hdemo
  (2030, 3, 1),
  -- 20:15 wrong minute
  (2029, 1, 1),
  -- 19:30 wrong hour
  (1930, 1, 1);
