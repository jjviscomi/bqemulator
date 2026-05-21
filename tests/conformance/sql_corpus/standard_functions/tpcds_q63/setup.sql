-- TPC-DS Q63 setup — Multi-table star with HAVING using AVG window-OVER
-- subquery + COV (STDDEV_SAMP / AVG). Spec params: d_year=1999, i_manager_id IN
-- (1,5,15,21,...).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1999, 1,  1200),
  (2451209, 1999, 2,  1201),
  (2451240, 1999, 3,  1202),
  (2451270, 1999, 4,  1203),
  (2451301, 1999, 5,  1204),
  (2451331, 1999, 6,  1205),
  (2451362, 1999, 7,  1206),
  (2451393, 1999, 8,  1207),
  (2451423, 1999, 9,  1208),
  (2451454, 1999, 10, 1209),
  (2451484, 1999, 11, 1210),
  (2451515, 1999, 12, 1211),
  (2451550, 2000, 1,  1212);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Store Alpha"),
  (2, "Store Beta");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_manager_id INT64, i_brand STRING, i_class STRING,
  i_category STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, 1,  "scholaramalgamalg #14", "personal",  "Books"),
  (2, 5,  "amalgimporto #1",       "classical", "Music"),
  (3, 15, "exportiunivamalg #9",   "portable",  "Electronics"),
  (4, 21, "edu packscholar #1",    "fragrances","Women"),
  (5, 22, "importoamalg #1",       "pants",     "Men"),
  (6, 28, "BrandF",                "history",   "Books");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_store_sk INT64, ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451179, 1, 1, NUMERIC "10.00"),
  (2451209, 1, 1, NUMERIC "20.00"),
  (2451240, 1, 1, NUMERIC "15.00"),
  (2451270, 1, 1, NUMERIC "25.00"),
  (2451301, 1, 1, NUMERIC "30.00"),
  (2451331, 1, 1, NUMERIC "12.00"),
  (2451362, 1, 1, NUMERIC "18.00"),
  (2451393, 1, 1, NUMERIC "22.00"),
  (2451423, 1, 1, NUMERIC "28.00"),
  (2451454, 1, 1, NUMERIC "100.00"),
  (2451484, 1, 1, NUMERIC "16.00"),
  (2451515, 1, 1, NUMERIC "14.00");
