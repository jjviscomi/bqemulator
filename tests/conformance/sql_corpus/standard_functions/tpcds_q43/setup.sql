-- TPC-DS Q43 setup — 7×SUM(CASE) pivot of sales by day of week. Spec params:
-- s_gmt_offset=-5, d_year=2000.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_day_name STRING
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451545, 2000, "Sunday"),
  (2451546, 2000, "Monday"),
  (2451547, 2000, "Tuesday"),
  (2451548, 2000, "Wednesday"),
  (2451549, 2000, "Thursday"),
  (2451550, 2000, "Friday"),
  (2451551, 2000, "Saturday"),
  (2451179, 1999, "Friday");

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING,
  s_store_name STRING, s_gmt_offset NUMERIC
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001", "Store Alpha", NUMERIC "-5"),
  (2, "S002", "Store Beta",  NUMERIC "-5"),
  (3, "S003", "Store Gamma", NUMERIC "-8");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451545, 1, NUMERIC "100.00"),
  (2451546, 1, NUMERIC  "80.00"),
  (2451547, 1, NUMERIC  "60.00"),
  (2451548, 1, NUMERIC  "70.00"),
  (2451549, 1, NUMERIC  "90.00"),
  (2451550, 1, NUMERIC "110.00"),
  (2451551, 1, NUMERIC "120.00"),
  (2451545, 2, NUMERIC  "50.00"),
  (2451546, 2, NUMERIC  "40.00"),
  (2451551, 2, NUMERIC  "30.00"),
  -- Out of scope
  (2451179, 1, NUMERIC  "10.00"),
  (2451545, 3, NUMERIC  "20.00");
