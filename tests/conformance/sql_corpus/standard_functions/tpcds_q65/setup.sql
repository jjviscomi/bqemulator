-- TPC-DS Q65 setup — store ⋈ item ⋈ store_sales ⋈ date_dim. Find
-- per-store low-performer items: revenue <= 0.1 * AVG(per-store revenue).
-- Spec params: d_month_seq BETWEEN 1176 AND 1187 (12-month window).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1176),
  (2451209, 1177),
  (2451240, 1178),
  (2451270, 1179),
  (2451301, 1180),
  (2451700, 1190); -- excluded (out of 12-month window)

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "able"),
  (2, "ation");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_desc STRING,
  i_current_price NUMERIC, i_wholesale_cost NUMERIC, i_brand STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Star Wars Trilogy",          NUMERIC "20.00", NUMERIC "10.00", "amalg #1"),
  (2, "Best Of The Beatles",        NUMERIC "15.00", NUMERIC  "8.00", "amalg #2"),
  (3, "Niche Avantgarde Album",     NUMERIC "12.00", NUMERIC  "6.00", "amalg #3"),
  (4, "Obscure Documentary",        NUMERIC "10.00", NUMERIC  "5.00", "amalg #4");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64, ss_item_sk INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- Store 1: item 1 high-revenue (drives AVG up), items 3 + 4 low-revenue
  (2451179, 1, 1, NUMERIC "500.00"),
  (2451209, 1, 1, NUMERIC "500.00"),
  (2451240, 1, 1, NUMERIC "500.00"),
  (2451270, 1, 2, NUMERIC "150.00"),
  (2451301, 1, 3, NUMERIC   "5.00"),  -- low — passes <= 0.1 * AVG
  (2451179, 1, 4, NUMERIC   "8.00"),  -- low — passes <= 0.1 * AVG
  -- Store 2: item 2 high-revenue, item 4 low-revenue
  (2451179, 2, 2, NUMERIC "300.00"),
  (2451209, 2, 2, NUMERIC "300.00"),
  (2451240, 2, 4, NUMERIC   "5.00"),  -- low — passes
  -- Out of d_month_seq window (excluded)
  (2451700, 1, 3, NUMERIC "999.00");
