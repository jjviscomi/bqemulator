-- TPC-DS Q8 setup — store_sales aggregation by zip prefix via SUBSTR(ca_zip, 1, 5)
-- IN a wide list. Spec uses 400 zip prefixes; we use 50 to exercise the wide-
-- IN-list translator path while keeping the recording small.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451910, 2000, 4),
  (2451911, 2001, 1),
  (2451920, 2001, 1);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING, s_zip STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Store Alpha",   "67592"),
  (2, "Store Beta",    "10044"),
  (3, "Store Gamma",   "90210");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_addr_sk INT64,
  c_preferred_cust_flag STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1, "Y"),
  (2, 2, "Y"),
  (3, 3, "N");

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_zip STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "67592"),
  (2, "10044"),
  (3, "90210");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64,
  ss_customer_sk INT64, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, 1, NUMERIC "100.00"),
  (2451920, 1, 1, NUMERIC  "80.00"),
  (2451911, 2, 2, NUMERIC  "60.00"),
  (2451911, 3, 3, NUMERIC  "40.00");
