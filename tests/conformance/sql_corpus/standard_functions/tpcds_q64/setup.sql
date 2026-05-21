-- TPC-DS Q64 setup — biggest TPC-DS cross-product. Joins catalog_sales,
-- catalog_returns, store_sales, store_returns, customer, customer_demographics
-- (×2 aliases), promotion, household_demographics (×2 aliases),
-- customer_address (×2 aliases), income_band (×2 aliases), item, store,
-- date_dim (×2 aliases). Spec params: d_year + 1.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451545, 1999),
  (2451910, 2000),
  (2452276, 2001);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING, s_zip STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Store Alpha", "37013"),
  (2, "Store Beta",  "37020");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_product_name STRING, i_color STRING,
  i_current_price NUMERIC
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Widget Alpha",  "purple", NUMERIC "60.00"),
  (2, "Widget Bravo",  "blue",   NUMERIC "50.00"),
  (3, "Gizmo Charlie", "green",  NUMERIC "40.00");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_cdemo_sk INT64, c_current_hdemo_sk INT64,
  c_current_addr_sk INT64,
  c_first_shipto_date_sk INT64, c_first_sales_date_sk INT64,
  c_first_name STRING, c_last_name STRING,
  c_birth_country STRING, c_preferred_cust_flag STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1, 1, 1, 2451545, 2451545, "Alice", "Anderson", "United States", "Y"),
  (2, 2, 2, 2, 2451910, 2451910, "Bob",   "Brown",    "Canada",        "N");

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_marital_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "M"),
  (2, "S");

CREATE OR REPLACE TABLE `${DATASET}.promotion` (
  p_promo_sk INT64
);
INSERT INTO `${DATASET}.promotion` VALUES (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_income_band_sk INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, 1),
  (2, 2);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_city STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "Memphis"),
  (2, "Nashville");

CREATE OR REPLACE TABLE `${DATASET}.income_band` (
  ib_income_band_sk INT64
);
INSERT INTO `${DATASET}.income_band` VALUES (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_item_sk INT64, ss_ticket_number INT64,
  ss_customer_sk INT64, ss_cdemo_sk INT64,
  ss_hdemo_sk INT64, ss_addr_sk INT64,
  ss_store_sk INT64, ss_promo_sk INT64,
  ss_sold_date_sk INT64,
  ss_wholesale_cost NUMERIC, ss_list_price NUMERIC,
  ss_coupon_amt NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (1, 1001, 1, 1, 1, 1, 1, 1, 2451545, NUMERIC "20.00", NUMERIC "60.00", NUMERIC "5.00"),
  (1, 1002, 1, 1, 1, 1, 1, 2, 2451910, NUMERIC "20.00", NUMERIC "60.00", NUMERIC "3.00"),
  (2, 1003, 2, 2, 2, 2, 2, 1, 2451910, NUMERIC "25.00", NUMERIC "50.00", NUMERIC "2.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_item_sk INT64, sr_ticket_number INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (1, 1001),
  (2, 1003);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_item_sk INT64, cs_order_number INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (1, 5001),
  (2, 5002);

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_item_sk INT64, cr_order_number INT64
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (1, 5001);
