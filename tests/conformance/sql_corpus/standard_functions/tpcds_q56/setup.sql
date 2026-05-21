-- TPC-DS Q56 setup — Multi-CTE INTERSECT across 3 channels. Spec params:
-- d_year=2001, d_moy=1, gmt_offset=-5, color IN ('slate','blanched','burnished').

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001, 1),
  (2451912, 2001, 1),
  (2451550, 2000, 11);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_gmt_offset NUMERIC
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, NUMERIC "-5"),
  (2, NUMERIC "-5"),
  (3, NUMERIC "-8");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_color STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1", "slate"),
  (2, "AAAA2", "blanched"),
  (3, "AAAA3", "burnished"),
  (4, "AAAA4", "red"),
  (5, "AAAA5", "slate");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_addr_sk INT64,
  ss_item_sk INT64, ss_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, 1, NUMERIC "100.00"),
  (2451911, 1, 2, NUMERIC  "75.00"),
  (2451912, 2, 3, NUMERIC  "60.00"),
  (2451912, 3, 1, NUMERIC  "20.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_addr_sk INT64,
  cs_item_sk INT64, cs_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451911, 1, 1, NUMERIC "50.00"),
  (2451911, 2, 2, NUMERIC "30.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_addr_sk INT64,
  ws_item_sk INT64, ws_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451911, 1, 1, NUMERIC "60.00"),
  (2451912, 2, 3, NUMERIC "40.00");
