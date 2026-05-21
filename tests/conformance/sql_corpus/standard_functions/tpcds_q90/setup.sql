-- TPC-DS Q90 setup — Web sales ratio of (morning hours 8-9) / (evening hours
-- 19-20) via two CROSS-JOIN-ed scalar count subqueries. Spec params:
-- hd_dep_count=6, wp_char_count BETWEEN 5000 AND 5200.

CREATE OR REPLACE TABLE `${DATASET}.time_dim` (
  t_time_sk INT64, t_hour INT64
);
INSERT INTO `${DATASET}.time_dim` VALUES
  (800, 8),
  (810, 8),
  (900, 9),
  (1900, 19),
  (1910, 19),
  (2000, 20);

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_dep_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, 6),
  (2, 4);

CREATE OR REPLACE TABLE `${DATASET}.web_page` (
  wp_web_page_sk INT64, wp_char_count INT64
);
INSERT INTO `${DATASET}.web_page` VALUES
  (1, 5100),
  (2, 6000);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_time_sk INT64, ws_ship_hdemo_sk INT64,
  ws_web_page_sk INT64
);
INSERT INTO `${DATASET}.web_sales` VALUES
  -- Morning (hour 8-9) entries: 3 matching rows
  (800, 1, 1),
  (810, 1, 1),
  (900, 1, 1),
  -- Evening (hour 19-20) entries: 2 matching rows
  (1900, 1, 1),
  (2000, 1, 1),
  -- Non-matching hdemo (count=4)
  (800, 2, 1),
  -- Non-matching web_page (char_count too high)
  (800, 1, 2);
