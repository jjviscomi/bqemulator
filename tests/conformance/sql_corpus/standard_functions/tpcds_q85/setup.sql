-- TPC-DS Q85 setup — Web returns aggregation by reason. Joins web_sales,
-- web_returns, customer_demographics (twice), customer_address, date_dim,
-- and the new **reason** table.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001),
  (2452276, 2002);

CREATE OR REPLACE TABLE `${DATASET}.reason` (
  r_reason_sk INT64, r_reason_desc STRING
);
INSERT INTO `${DATASET}.reason` VALUES
  (1, "Did not fit"),
  (2, "Wrong size"),
  (3, "Defective");

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_country STRING,
  ca_gmt_offset NUMERIC, ca_state STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "United States", NUMERIC "-5", "TN"),
  (2, "United States", NUMERIC "-5", "GA"),
  (3, "Canada",        NUMERIC "-5", "ON");

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_marital_status STRING,
  cd_education_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "M", "Advanced Degree"),
  (2, "S", "College"),
  (3, "W", "2 yr Degree");

CREATE OR REPLACE TABLE `${DATASET}.web_page` (
  wp_web_page_sk INT64
);
INSERT INTO `${DATASET}.web_page` VALUES
  (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_order_number INT64, ws_web_page_sk INT64,
  ws_sales_price NUMERIC, ws_net_profit NUMERIC,
  ws_quantity INT64
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451911, 1, 1001, 1,  NUMERIC "100.00", NUMERIC  "20.00", 5),
  (2451911, 2, 1002, 1,  NUMERIC  "80.00", NUMERIC  "15.00", 3),
  (2451911, 3, 1003, 2,  NUMERIC "120.00", NUMERIC  "25.00", 7);

CREATE OR REPLACE TABLE `${DATASET}.web_returns` (
  wr_returned_date_sk INT64, wr_item_sk INT64,
  wr_order_number INT64, wr_reason_sk INT64,
  wr_refunded_cdemo_sk INT64, wr_refunded_addr_sk INT64,
  wr_returning_cdemo_sk INT64, wr_fee NUMERIC,
  wr_refunded_cash NUMERIC
);
INSERT INTO `${DATASET}.web_returns` VALUES
  (2451911, 1, 1001, 1, 1, 1, 1, NUMERIC "5.00", NUMERIC "30.00"),
  (2451911, 2, 1002, 2, 2, 2, 2, NUMERIC "3.00", NUMERIC "20.00");
