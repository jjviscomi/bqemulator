-- TPC-DS Q77 setup — ROLLUP across (channel, id) combined with 3-channel
-- UNION ALL. Spec params: 30-day window from 2000-08-23.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451780, DATE "2000-08-23"),
  (2451781, DATE "2000-08-24"),
  (2451800, DATE "2000-09-12"),
  (2451809, DATE "2000-09-21"),
  (2451850, DATE "2000-11-01");

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001"),
  (2, "S002");

CREATE OR REPLACE TABLE `${DATASET}.catalog_page` (
  cp_catalog_page_sk INT64, cp_catalog_page_id STRING
);
INSERT INTO `${DATASET}.catalog_page` VALUES
  (1, "C001"),
  (2, "C002");

CREATE OR REPLACE TABLE `${DATASET}.web_site` (
  web_site_sk INT64, web_site_id STRING
);
INSERT INTO `${DATASET}.web_site` VALUES
  (1, "W001"),
  (2, "W002");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64,
  ss_ext_sales_price NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451780, 1, NUMERIC "100.00", NUMERIC "20.00"),
  (2451781, 1, NUMERIC  "80.00", NUMERIC "15.00"),
  (2451800, 2, NUMERIC "120.00", NUMERIC "25.00"),
  (2451850, 1, NUMERIC  "30.00", NUMERIC  "5.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_store_sk INT64,
  sr_return_amt NUMERIC, sr_net_loss NUMERIC
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (2451781, 1, NUMERIC "20.00", NUMERIC  "5.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_catalog_page_sk INT64,
  cs_ext_sales_price NUMERIC, cs_net_profit NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451780, 1, NUMERIC "50.00", NUMERIC "10.00"),
  (2451781, 2, NUMERIC "70.00", NUMERIC "14.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_returned_date_sk INT64, cr_catalog_page_sk INT64,
  cr_return_amount NUMERIC, cr_net_loss NUMERIC
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (2451781, 1, NUMERIC "5.00", NUMERIC "1.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_web_site_sk INT64,
  ws_ext_sales_price NUMERIC, ws_net_profit NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451780, 1, NUMERIC "60.00", NUMERIC "12.00"),
  (2451781, 2, NUMERIC "80.00", NUMERIC "16.00");

CREATE OR REPLACE TABLE `${DATASET}.web_returns` (
  wr_returned_date_sk INT64, wr_return_amt NUMERIC, wr_net_loss NUMERIC
);
INSERT INTO `${DATASET}.web_returns` VALUES
  (2451781, NUMERIC "8.00", NUMERIC "2.00");
