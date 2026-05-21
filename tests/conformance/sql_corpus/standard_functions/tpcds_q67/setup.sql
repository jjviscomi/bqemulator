-- TPC-DS Q67 setup — GROUPING SETS over (i_category, i_class, i_brand,
-- i_product_name, d_year, d_qoy, d_moy, s_store_id) + RANK OVER PARTITION BY
-- i_category ORDER BY sumsales DESC. Spec params: d_month_seq IN 12 values.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64, d_moy INT64,
  d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1999, 1, 1,  1200),
  (2451209, 1999, 1, 2,  1201),
  (2451240, 1999, 1, 3,  1202),
  (2451270, 1999, 2, 4,  1203),
  (2451301, 1999, 2, 5,  1204),
  (2451331, 1999, 2, 6,  1205),
  (2451362, 1999, 3, 7,  1206),
  (2451393, 1999, 3, 8,  1207),
  (2451423, 1999, 3, 9,  1208),
  (2451454, 1999, 4, 10, 1209),
  (2451484, 1999, 4, 11, 1210),
  (2451515, 1999, 4, 12, 1211);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001"),
  (2, "S002");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_category STRING, i_class STRING,
  i_brand STRING, i_product_name STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Sports", "athletic", "BrandA", "Prod1"),
  (2, "Sports", "team",     "BrandB", "Prod2"),
  (3, "Music",  "rock",     "BrandC", "Prod3"),
  (4, "Music",  "jazz",     "BrandD", "Prod4"),
  (5, "Books",  "fiction",  "BrandE", "Prod5");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_store_sk INT64, ss_sales_price NUMERIC, ss_quantity INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451179, 1, 1, NUMERIC "10.00", 3),
  (2451209, 1, 1, NUMERIC "20.00", 2),
  (2451240, 2, 1, NUMERIC "15.00", 1),
  (2451270, 2, 1, NUMERIC "25.00", 5),
  (2451301, 3, 2, NUMERIC "30.00", 4),
  (2451362, 4, 2, NUMERIC "12.00", 3),
  (2451423, 5, 1, NUMERIC "18.00", 2),
  (2451454, 1, 2, NUMERIC "22.00", 1),
  (2451484, 2, 1, NUMERIC "28.00", 3),
  (2451515, 3, 1, NUMERIC "16.00", 2);
