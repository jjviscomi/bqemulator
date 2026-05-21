-- TPC-DS Q97 setup — FULL OUTER JOIN of store + catalog CTEs grouped by
-- (customer_sk, item_sk). Reports counts of catalog-only / store-only / both.
-- Spec params: d_month_seq=1200..1211.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200),
  (2451209, 1201),
  (2451240, 1202),
  (2451550, 1212);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64, ss_item_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- (customer 1, item 1) in store only
  (2451179, 1, 1),
  -- (customer 2, item 2) in store + catalog (overlap)
  (2451179, 2, 2),
  -- (customer 3, item 3) in store only
  (2451209, 3, 3),
  -- duplicate to test DISTINCT
  (2451209, 1, 1);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64, cs_item_sk INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- (customer 2, item 2) overlap with store
  (2451179, 2, 2),
  -- (customer 4, item 4) catalog only
  (2451240, 4, 4),
  -- (customer 5, item 5) catalog only
  (2451240, 5, 5);
