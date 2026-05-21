-- TPC-DS Q36 setup — GROUP BY ROLLUP(i_category, i_class) + RANK over
-- PARTITION BY GROUPING expression. Spec params: d_year=2001, s_state='TN'.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001),
  (2451912, 2001),
  (2451550, 2000);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_state STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "TN"),
  (2, "TN"),
  (3, "CA");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_category STRING, i_class STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Sports", "athletic"),
  (2, "Sports", "team"),
  (3, "Music",  "rock"),
  (4, "Music",  "jazz"),
  (5, "Books",  "fiction"),
  (6, "Books",  "history");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64, ss_store_sk INT64,
  ss_ext_sales_price NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451911, 1, 1, NUMERIC "100.00", NUMERIC "10.00"),
  (2451911, 2, 1, NUMERIC  "80.00", NUMERIC  "8.00"),
  (2451912, 3, 1, NUMERIC  "70.00", NUMERIC "14.00"),
  (2451912, 4, 2, NUMERIC  "60.00", NUMERIC  "9.00"),
  (2451911, 5, 1, NUMERIC  "50.00", NUMERIC  "5.00"),
  (2451912, 6, 2, NUMERIC  "40.00", NUMERIC  "6.00"),
  (2451550, 1, 3, NUMERIC  "20.00", NUMERIC  "2.00");
