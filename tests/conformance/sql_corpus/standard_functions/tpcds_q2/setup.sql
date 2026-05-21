-- TPC-DS Q2 setup — Week-over-week self-join of a wstscs CTE (web+catalog
-- UNION ALL) projected through SUM(CASE WHEN d_day_name) per week. Spec
-- params: d_year=2001.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_day_name STRING, d_week_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  -- Week 5300 (7 days)
  (2452276, 2001, "Sunday",    5300),
  (2452277, 2001, "Monday",    5300),
  (2452278, 2001, "Tuesday",   5300),
  (2452279, 2001, "Wednesday", 5300),
  (2452280, 2001, "Thursday",  5300),
  (2452281, 2001, "Friday",    5300),
  (2452282, 2001, "Saturday",  5300),
  -- Week 5301 (7 days)
  (2452283, 2001, "Sunday",    5301),
  (2452284, 2001, "Monday",    5301),
  (2452285, 2001, "Tuesday",   5301),
  (2452286, 2001, "Wednesday", 5301),
  (2452287, 2001, "Thursday",  5301),
  (2452288, 2001, "Friday",    5301),
  (2452289, 2001, "Saturday",  5301),
  -- 2000 baseline week (5300 - 52 = 5248) for the matching condition
  (2451908, 2000, "Sunday",    5248),
  (2451909, 2000, "Monday",    5248),
  (2451910, 2000, "Tuesday",   5248),
  (2451911, 2000, "Wednesday", 5248),
  (2451912, 2000, "Thursday",  5248),
  (2451913, 2000, "Friday",    5248),
  (2451914, 2000, "Saturday",  5248);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2452276, NUMERIC "100.00"),
  (2452277, NUMERIC  "80.00"),
  (2452278, NUMERIC  "60.00"),
  (2452283, NUMERIC "120.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2452276, NUMERIC "50.00"),
  (2452284, NUMERIC "70.00");
