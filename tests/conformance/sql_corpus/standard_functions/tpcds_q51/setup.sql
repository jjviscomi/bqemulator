-- TPC-DS Q51 setup — FULL OUTER JOIN of cumulative-max windows over
-- web_sales and store_sales by (item_sk, date). Spec params: d_month_seq
-- BETWEEN 1200 AND 1200+11.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, DATE "1999-01-01", 1200),
  (2451180, DATE "1999-01-02", 1200),
  (2451209, DATE "1999-02-01", 1201),
  (2451240, DATE "1999-03-01", 1202);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451179, 1, NUMERIC "100.00"),
  (2451180, 1, NUMERIC  "80.00"),
  (2451209, 1, NUMERIC "150.00"),
  (2451240, 1, NUMERIC "120.00"),
  (2451179, 2, NUMERIC  "50.00"),
  (2451180, 2, NUMERIC  "60.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451179, 1, NUMERIC  "40.00"),
  (2451209, 1, NUMERIC "200.00"),
  -- item 3 only in web (no store match)
  (2451180, 3, NUMERIC  "30.00");
