-- TPC-DS Q4 setup — 3-CTE year_total pattern across store/catalog/web
-- channels. Spec params: year IN (2001,2002), preferred_cust_flag='Y'.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001, DATE "2001-01-01"),
  (2452276, 2002, DATE "2002-01-01"),
  (2452550, 2002, DATE "2002-10-02");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING,
  c_first_name STRING, c_last_name STRING,
  c_preferred_cust_flag STRING, c_birth_country STRING,
  c_login STRING, c_email_address STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1", "Alice",   "Anderson", "Y", "USA", "alogin",  "a@x"),
  (2, "AAAA2", "Bob",     "Brown",    "Y", "CAN", "blogin",  "b@x"),
  (3, "AAAA3", "Carol",   "Clarke",   "N", "USA", "clogin",  "c@x");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_ext_discount_amt NUMERIC, ss_ext_sales_price NUMERIC,
  ss_ext_wholesale_cost NUMERIC, ss_ext_list_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, NUMERIC "5.00", NUMERIC "100.00", NUMERIC "40.00", NUMERIC "105.00"),
  (2452276, 1, NUMERIC "3.00", NUMERIC "150.00", NUMERIC "60.00", NUMERIC "153.00"),
  (2451911, 2, NUMERIC "2.00", NUMERIC  "80.00", NUMERIC "30.00", NUMERIC  "82.00"),
  (2452276, 2, NUMERIC "1.00", NUMERIC  "90.00", NUMERIC "40.00", NUMERIC  "91.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_ext_discount_amt NUMERIC, cs_ext_sales_price NUMERIC,
  cs_ext_wholesale_cost NUMERIC, cs_ext_list_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451911, 1, NUMERIC "2.00", NUMERIC "50.00", NUMERIC "20.00", NUMERIC "52.00"),
  (2452276, 1, NUMERIC "3.00", NUMERIC "70.00", NUMERIC "30.00", NUMERIC "73.00"),
  (2451911, 2, NUMERIC "1.00", NUMERIC "40.00", NUMERIC "20.00", NUMERIC "41.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_customer_sk INT64,
  ws_ext_discount_amt NUMERIC, ws_ext_sales_price NUMERIC,
  ws_ext_wholesale_cost NUMERIC, ws_ext_list_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451911, 1, NUMERIC "4.00", NUMERIC "60.00", NUMERIC "20.00", NUMERIC "64.00"),
  (2452276, 1, NUMERIC "2.00", NUMERIC "80.00", NUMERIC "30.00", NUMERIC "82.00"),
  (2451911, 2, NUMERIC "1.00", NUMERIC "30.00", NUMERIC "10.00", NUMERIC "31.00");
