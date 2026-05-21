-- TPC-DS Q29 setup — store ⋈ store_sales ⋈ store_returns ⋈ catalog_sales
-- ⋈ date_dim ×3 ⋈ store ⋈ item. Spec params: month 9, year 1999, year
-- IN (1999, 2000, 2001).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451423, 1999, 9),
  (2451424, 1999, 9),
  (2451550, 2000, 1),
  (2451910, 2000, 12),
  (2452020, 2001, 4),
  (2452276, 2002, 1);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING, s_store_name STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001", "Store Alpha"),
  (2, "S002", "Store Beta");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1", "Item One"),
  (2, "AAAA2", "Item Two");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64, ss_item_sk INT64,
  ss_customer_sk INT64, ss_ticket_number INT64, ss_quantity INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451423, 1, 1, 1, 1001, 5),
  (2451423, 1, 2, 2, 1002, 3),
  (2451424, 2, 1, 3, 1003, 4);

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_item_sk INT64,
  sr_customer_sk INT64, sr_ticket_number INT64, sr_return_quantity INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (2451550, 1, 1, 1001, 1),
  (2451550, 2, 2, 1002, 1);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_item_sk INT64, cs_quantity INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2452020, 1, 1, 2),
  (2451910, 2, 2, 3),
  (2452276, 1, 1, 1);
