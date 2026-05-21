-- TPC-DS Q88 setup — 4-table star repeated 8 times via cross-join.
-- store_sales ⋈ household_demographics ⋈ time_dim ⋈ store.
-- Spec params: t_hour=8..12 + t_minute thresholds; s_store_name='ese';
-- household_demographics filter on (hd_dep_count, hd_vehicle_count).

CREATE OR REPLACE TABLE `${DATASET}.time_dim` (
  t_time_sk INT64, t_hour INT64, t_minute INT64
);
INSERT INTO `${DATASET}.time_dim` VALUES
  -- 8:30-8:59
  (1830, 8, 30),
  (1845, 8, 45),
  -- 9:00-9:29
  (1900, 9, 0),
  (1915, 9, 15),
  -- 9:30-9:59
  (1930, 9, 30),
  -- 10:00-10:29
  (2000, 10, 0),
  -- 10:30-10:59
  (2030, 10, 30),
  -- 11:00-11:29
  (2100, 11, 0),
  -- 11:30-11:59
  (2130, 11, 30),
  -- 12:00-12:29
  (2200, 12, 0);

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_dep_count INT64, hd_vehicle_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  -- dep_count=4, vehicle_count<=6 (matches +2 clause)
  (1, 4, 4),
  (2, 4, 5),
  -- dep_count=2, vehicle_count<=4
  (3, 2, 2),
  (4, 2, 3),
  -- dep_count=0, vehicle_count<=2
  (5, 0, 2),
  -- Out of all 3 clauses
  (6, 7, 7);

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
  -- 8:30-8:59 store=1 hdemo matching
  (1830, 1, 1),
  (1845, 3, 1),
  -- 9:00-9:29 store=1 hdemo matching
  (1900, 5, 1),
  (1915, 1, 1),
  -- 9:30-9:59
  (1930, 3, 1),
  -- 10:00-10:29
  (2000, 5, 1),
  -- 10:30-10:59
  (2030, 1, 1),
  -- 11:00-11:29
  (2100, 3, 1),
  -- 11:30-11:59
  (2130, 5, 1),
  -- 12:00-12:29
  (2200, 1, 1),
  -- one in store=2 (excluded)
  (1830, 1, 2);
