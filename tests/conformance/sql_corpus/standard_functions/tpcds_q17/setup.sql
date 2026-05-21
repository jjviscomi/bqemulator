-- TPC-DS Q17 setup — 3-channel cross-product with STDDEV_SAMP/AVG coefficient
-- of variation (CV) per (item, store, state, quantity). Spec params:
-- store_sales quarter d_qoy=1 d_year=2001, store_returns d_year IN (2001/2/3),
-- catalog_sales d_year IN (2001/2/3).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64, d_quarter_name STRING
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001, 1, "2001Q1"),
  (2451912, 2001, 1, "2001Q1"),
  (2452000, 2001, 2, "2001Q2"),
  (2452276, 2002, 1, "2002Q1"),
  (2452500, 2002, 4, "2002Q4"),
  (2452641, 2003, 1, "2003Q1");

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING, s_state STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001", "TN"),
  (2, "S002", "TN");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1", "Item One"),
  (2, "AAAA2", "Item Two"),
  (3, "AAAA3", "Item Three");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_store_sk INT64, ss_item_sk INT64,
  ss_ticket_number INT64, ss_quantity INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, 1, 1, 1001, 5),
  (2451911, 2, 1, 1, 1002, 7),
  (2451912, 3, 1, 1, 1003, 9),
  (2451911, 1, 2, 2, 1004, 4),
  (2451911, 2, 2, 2, 1005, 6);

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_customer_sk INT64,
  sr_item_sk INT64, sr_ticket_number INT64,
  sr_return_quantity INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (2452000, 1, 1, 1001, 1),
  (2452000, 2, 1, 1002, 2),
  (2452276, 3, 1, 1003, 1),
  (2452641, 1, 2, 1004, 1),
  (2452641, 2, 2, 1005, 2);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_item_sk INT64, cs_quantity INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2452000, 1, 1, 3),
  (2452276, 2, 1, 5),
  (2452500, 3, 1, 7),
  (2452641, 1, 2, 4),
  (2452641, 2, 2, 6);
