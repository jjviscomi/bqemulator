-- TPC-DS Q75 setup — Multi-CTE EXCEPT for product/year sales/returns analysis.
-- Spec params: i_category=Sports, year IN (2001,2002).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001),
  (2451912, 2001),
  (2452276, 2002),
  (2452277, 2002);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING,
  i_brand_id INT64, i_class_id INT64,
  i_category_id INT64, i_manufact_id INT64,
  i_category STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1", 101, 1, 1, 11, "Sports"),
  (2, "AAAA2", 102, 1, 1, 11, "Sports"),
  (3, "AAAA3", 103, 2, 1, 12, "Sports"),
  (4, "AAAA4", 104, 1, 2, 11, "Music");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_item_sk INT64,
  cs_quantity INT64, cs_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451911, 1, 10, NUMERIC "100.00"),
  (2451911, 2,  5, NUMERIC  "50.00"),
  (2451912, 3,  3, NUMERIC  "30.00"),
  (2452276, 1,  8, NUMERIC  "80.00"),
  (2452276, 2,  4, NUMERIC  "40.00");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_quantity INT64, ss_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, 12, NUMERIC "120.00"),
  (2451912, 2,  6, NUMERIC  "60.00"),
  (2452276, 1, 11, NUMERIC "110.00"),
  (2452277, 3,  2, NUMERIC  "20.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_quantity INT64, ws_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451911, 1,  7, NUMERIC  "70.00"),
  (2452276, 1,  6, NUMERIC  "60.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_item_sk INT64, cr_order_number INT64,
  cr_return_quantity INT64, cr_return_amount NUMERIC
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (1, 5001, 1, NUMERIC "10.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_item_sk INT64, sr_ticket_number INT64,
  sr_return_quantity INT64, sr_return_amt NUMERIC
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (2, 3001, 1, NUMERIC "10.00");

CREATE OR REPLACE TABLE `${DATASET}.web_returns` (
  wr_item_sk INT64, wr_order_number INT64,
  wr_return_quantity INT64, wr_return_amt NUMERIC
);
INSERT INTO `${DATASET}.web_returns` VALUES
  (1, 8001, 1, NUMERIC "10.00");
