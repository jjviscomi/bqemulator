-- TPC-DS Q20 setup — catalog_sales ⋈ item ⋈ date_dim with item-revenue ratio
-- via SUM(cs_ext_sales_price) OVER (PARTITION BY i_class). Spec params:
-- i_category IN ('Sports','Books','Home'); d_date between 1999-02-22 and
-- 1999-02-22 + 30 days.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451232, DATE "1999-02-22"),
  (2451240, DATE "1999-03-02"),
  (2451250, DATE "1999-03-12"),
  (2451262, DATE "1999-03-24"),
  (2451300, DATE "1999-05-01"); -- out of 30-day window

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING,
  i_current_price NUMERIC, i_class STRING, i_category STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "ITEM001", "Sports football",     NUMERIC "20.00", "athletic",  "Sports"),
  (2, "ITEM002", "Sports tennis racket",NUMERIC "55.00", "athletic",  "Sports"),
  (3, "ITEM003", "Books novel",         NUMERIC "15.00", "fiction",   "Books"),
  (4, "ITEM004", "Books cookbook",      NUMERIC "25.00", "fiction",   "Books"),
  (5, "ITEM005", "Home lamp",           NUMERIC "40.00", "lighting",  "Home"),
  (6, "ITEM006", "Music CD",            NUMERIC "12.00", "classical", "Music"); -- excluded by category

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_item_sk INT64,
  cs_ext_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- class=athletic: item 1 + item 2
  (2451232, 1, NUMERIC "120.00"),
  (2451240, 1, NUMERIC  "80.00"),
  (2451250, 2, NUMERIC "200.00"),
  -- class=fiction: item 3 + item 4
  (2451240, 3, NUMERIC  "70.00"),
  (2451250, 4, NUMERIC "130.00"),
  -- class=lighting: item 5
  (2451262, 5, NUMERIC "150.00"),
  -- excluded by date window
  (2451300, 1, NUMERIC "999.00"),
  -- excluded by category (Music)
  (2451232, 6, NUMERIC "999.00");
