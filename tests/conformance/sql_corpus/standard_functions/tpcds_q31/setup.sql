-- TPC-DS Q31 setup — multi-CTE pipeline computing year-over-year sales
-- ratio by ca_county. Spec params: year=2000, qoy IN (1,2,3).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  -- 2000 Q1, Q2, Q3
  (2451550, 2000, 1),
  (2451650, 2000, 2),
  (2451750, 2000, 3),
  -- 2001 Q1, Q2, Q3
  (2451915, 2001, 1),
  (2452015, 2001, 2),
  (2452115, 2001, 3),
  -- 2002 (out of range)
  (2452280, 2002, 1);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_county STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "Williamson County"),
  (2, "Franklin Parish"),
  (3, "Other County");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_addr_sk INT64,
  ss_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451550, 1, NUMERIC "100.00"),
  (2451650, 1, NUMERIC "200.00"),
  (2451750, 1, NUMERIC "300.00"),
  (2451915, 1, NUMERIC "120.00"),
  (2452015, 1, NUMERIC "260.00"),
  (2452115, 1, NUMERIC "330.00"),
  (2451550, 2, NUMERIC  "50.00"),
  (2451650, 2, NUMERIC  "60.00"),
  (2451750, 2, NUMERIC  "70.00"),
  (2451915, 2, NUMERIC  "75.00"),
  (2452015, 2, NUMERIC  "80.00"),
  (2452115, 2, NUMERIC  "85.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_addr_sk INT64,
  ws_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451550, 1, NUMERIC "40.00"),
  (2451650, 1, NUMERIC "60.00"),
  (2451750, 1, NUMERIC "80.00"),
  (2451915, 1, NUMERIC "70.00"),
  (2452015, 1, NUMERIC "90.00"),
  (2452115, 1, NUMERIC "100.00"),
  (2451550, 2, NUMERIC "30.00"),
  (2451650, 2, NUMERIC "20.00"),
  (2451750, 2, NUMERIC "10.00"),
  (2451915, 2, NUMERIC "15.00"),
  (2452015, 2, NUMERIC "12.00"),
  (2452115, 2, NUMERIC "8.00");
