-- TPC-DS Q87 setup — 3-way EXCEPT chain to count unique customers who bought
-- in store but not in catalog and not in web. Spec params: d_month_seq=1200..1211.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, DATE "1999-01-01", 1200),
  (2451209, DATE "1999-02-01", 1201),
  (2451240, DATE "1999-03-01", 1202);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_first_name STRING, c_last_name STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "Alice", "Anderson"),
  (2, "Bob",   "Brown"),
  (3, "Carol", "Clarke"),
  (4, "Dave",  "Davis");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- Customer 1 buys only at store
  (2451179, 1),
  -- Customer 2 buys store + catalog
  (2451179, 2),
  -- Customer 3 buys store + catalog + web
  (2451179, 3),
  -- Customer 4 buys only at store
  (2451209, 4);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451179, 2),
  (2451209, 3);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_customer_sk INT64
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451179, 3);
