-- TPC-DS Q47 setup — Multi-CTE with LAG window function, AVG window function,
-- and CTE self-join. Spec params: d_year=1999/2000, d_moy across months.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1999, 1),
  (2451209, 1999, 2),
  (2451240, 1999, 3),
  (2451270, 1999, 4),
  (2451301, 1999, 5),
  (2451331, 1999, 6),
  (2451362, 1999, 7),
  (2451393, 1999, 8),
  (2451423, 1999, 9),
  (2451454, 1999, 10),
  (2451484, 1999, 11),
  (2451515, 1999, 12),
  (2451545, 2000, 1),
  (2451576, 2000, 2),
  (2451605, 2000, 3),
  (2451636, 2000, 4),
  (2451666, 2000, 5),
  (2451697, 2000, 6),
  (2451727, 2000, 7),
  (2451758, 2000, 8),
  (2451789, 2000, 9),
  (2451819, 2000, 10),
  (2451850, 2000, 11),
  (2451880, 2000, 12);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_category STRING, i_brand STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Sports", "BrandA"),
  (2, "Sports", "BrandB"),
  (3, "Music",  "BrandC");

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING, s_company_name STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Store Alpha", "Co1"),
  (2, "Store Beta",  "Co1");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_store_sk INT64, ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- item 1 (Sports/BrandA) at store 1 across all months of 1999
  (2451179, 1, 1, NUMERIC "10.00"),
  (2451209, 1, 1, NUMERIC "20.00"),
  (2451240, 1, 1, NUMERIC "30.00"),
  (2451270, 1, 1, NUMERIC "40.00"),
  (2451301, 1, 1, NUMERIC "50.00"),
  (2451331, 1, 1, NUMERIC "60.00"),
  (2451362, 1, 1, NUMERIC "70.00"),
  (2451393, 1, 1, NUMERIC "80.00"),
  (2451423, 1, 1, NUMERIC "90.00"),
  (2451454, 1, 1, NUMERIC "100.00"),
  (2451484, 1, 1, NUMERIC "110.00"),
  (2451515, 1, 1, NUMERIC "120.00"),
  -- item 1 in 2000 with a notable dip in Mar
  (2451545, 1, 1, NUMERIC "130.00"),
  (2451576, 1, 1, NUMERIC "140.00"),
  (2451605, 1, 1, NUMERIC "10.00"),
  (2451636, 1, 1, NUMERIC "160.00"),
  (2451666, 1, 1, NUMERIC "170.00"),
  (2451697, 1, 1, NUMERIC "180.00"),
  (2451727, 1, 1, NUMERIC "190.00"),
  (2451758, 1, 1, NUMERIC "200.00"),
  (2451789, 1, 1, NUMERIC "210.00"),
  (2451819, 1, 1, NUMERIC "220.00"),
  (2451850, 1, 1, NUMERIC "230.00"),
  (2451880, 1, 1, NUMERIC "240.00");
