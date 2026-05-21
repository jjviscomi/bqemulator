-- TPC-DS Q42 setup — 3-table star (date_dim ⋈ store_sales ⋈ item).
-- Spec params: d_moy=11, d_year=2000, i_manager_id=1.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451850, DATE "2000-11-01", 2000, 11),
  (2451851, DATE "2000-11-02", 2000, 11),
  (2451852, DATE "2000-11-03", 2000, 11),
  (2451820, DATE "2000-10-15", 2000, 10),
  (2452215, DATE "2001-11-01", 2001, 11);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64,
  i_category_id INT64, i_category STRING,
  i_class_id INT64, i_class STRING,
  i_manager_id INT64, i_manufact_id INT64
);
INSERT INTO `${DATASET}.item` VALUES
  (1, 1, "Sports",   11, "class-sport-1",  1, 100),
  (2, 2, "Music",    21, "class-music-1",  1, 101),
  (3, 3, "Books",    31, "class-book-1",   1, 102),
  (4, 1, "Sports",   12, "class-sport-2",  2, 103),
  (5, 4, "Jewelry",  41, "class-jewel-1",  1, 104);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451850, 1, NUMERIC "100.00"),
  (2451850, 2, NUMERIC  "75.00"),
  (2451851, 3, NUMERIC  "60.00"),
  (2451851, 1, NUMERIC  "40.00"),
  (2451852, 5, NUMERIC  "80.00"),
  (2451852, 2, NUMERIC  "25.00"),
  (2451820, 1, NUMERIC  "10.00"),
  (2452215, 1, NUMERIC  "50.00"),
  (2451850, 4, NUMERIC  "30.00");
