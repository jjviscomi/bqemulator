-- TPC-DS Q49 setup — Top return-percentage items from 3 channels combined.
-- web_sales/web_returns + catalog_sales/catalog_returns + store_sales/store_returns.
-- Spec params: d_year=2001, d_moy=12, return_ratio<1.0, return_rank<=10, currency_rank<=10.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452245, 2001, 12),
  (2452246, 2001, 12),
  (2452275, 2002, 1);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_order_number INT64, ws_quantity INT64,
  ws_net_paid NUMERIC, ws_net_profit NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2452245, 1, 1001, 10, NUMERIC "100.00", NUMERIC "20.00"),
  (2452245, 2, 1002, 20, NUMERIC "150.00", NUMERIC "30.00"),
  (2452246, 3, 1003,  5, NUMERIC  "50.00", NUMERIC "10.00");

CREATE OR REPLACE TABLE `${DATASET}.web_returns` (
  wr_returned_date_sk INT64, wr_item_sk INT64,
  wr_order_number INT64, wr_return_quantity INT64,
  wr_return_amt NUMERIC
);
INSERT INTO `${DATASET}.web_returns` VALUES
  (2452246, 1, 1001, 2, NUMERIC "20.00"),
  (2452246, 2, 1002, 5, NUMERIC "30.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_item_sk INT64,
  cs_order_number INT64, cs_quantity INT64,
  cs_net_paid NUMERIC, cs_net_profit NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2452245, 1, 2001, 8, NUMERIC "80.00", NUMERIC "16.00"),
  (2452246, 2, 2002, 6, NUMERIC "60.00", NUMERIC "12.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_returned_date_sk INT64, cr_item_sk INT64,
  cr_order_number INT64, cr_return_quantity INT64,
  cr_return_amount NUMERIC
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (2452246, 1, 2001, 1, NUMERIC "10.00");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_ticket_number INT64, ss_quantity INT64,
  ss_net_paid NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2452245, 1, 3001, 4, NUMERIC "40.00", NUMERIC  "8.00"),
  (2452246, 2, 3002, 3, NUMERIC "30.00", NUMERIC  "6.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_item_sk INT64,
  sr_ticket_number INT64, sr_return_quantity INT64,
  sr_return_amt NUMERIC
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (2452246, 1, 3001, 1, NUMERIC "10.00");
