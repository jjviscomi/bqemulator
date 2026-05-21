-- TPC-DS Q53 setup — 4×SUM(CASE) quarterly pivot + AVG(SUM(...)) OVER
-- (PARTITION BY i_manufact_id). Spec params: d_month_seq=1200..1211, item
-- category/class buckets.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200, 1),
  (2451270, 1203, 2),
  (2451362, 1206, 3),
  (2451454, 1209, 4),
  (2451550, 1212, 1);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_manufact_id INT64, i_category STRING,
  i_class STRING, i_brand STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, 101, "Books",       "personal",  "scholaramalgamalg #14"),
  (2, 102, "Children",    "portable",  "scholaramalgamalg #7"),
  (3, 103, "Electronics", "reference", "exportiunivamalg #9"),
  (4, 201, "Women",       "accessories","amalgimporto #1"),
  (5, 202, "Men",         "pants",     "importoamalg #1");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- item 1 (manuf 101, BooksPersonal) Q1+Q2+Q3+Q4
  (2451179, 1, NUMERIC "100.00"),
  (2451270, 1, NUMERIC "120.00"),
  (2451362, 1, NUMERIC "300.00"),
  (2451454, 1, NUMERIC "130.00"),
  -- item 3 (manuf 103, ElectronicsReference) Q1+Q2 only
  (2451179, 3, NUMERIC  "80.00"),
  (2451270, 3, NUMERIC  "90.00"),
  -- item 4 (manuf 201, WomenAccessories) all 4 Q
  (2451179, 4, NUMERIC  "50.00"),
  (2451270, 4, NUMERIC  "55.00"),
  (2451362, 4, NUMERIC  "60.00"),
  (2451454, 4, NUMERIC  "65.00");
