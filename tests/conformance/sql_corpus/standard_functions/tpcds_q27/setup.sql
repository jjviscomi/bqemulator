-- TPC-DS Q27 setup — GROUP BY ROLLUP(i_item_id, s_state) + GROUPING(s_state).
-- Spec params: cd_gender='F', cd_marital_status='W', cd_education_status='Primary',
-- d_year=1998, s_state IN ('TN','GA').

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2450816, 1998),
  (2450900, 1998),
  (2451180, 1999);

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_gender STRING,
  cd_marital_status STRING, cd_education_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "F", "W", "Primary"),
  (2, "M", "M", "College");

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_state STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "TN"),
  (2, "TN"),
  (3, "GA"),
  (4, "CA");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1"),
  (2, "AAAA2");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_cdemo_sk INT64,
  ss_store_sk INT64, ss_item_sk INT64,
  ss_quantity INT64, ss_list_price NUMERIC,
  ss_coupon_amt NUMERIC, ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2450816, 1, 1, 1, 3, NUMERIC "10.00", NUMERIC "0.50", NUMERIC  "9.50"),
  (2450816, 1, 2, 2, 2, NUMERIC "15.00", NUMERIC "1.00", NUMERIC "14.00"),
  (2450900, 1, 3, 1, 5, NUMERIC "20.00", NUMERIC "0.00", NUMERIC "20.00"),
  (2450900, 2, 4, 1, 1, NUMERIC "12.00", NUMERIC "0.00", NUMERIC "12.00");
