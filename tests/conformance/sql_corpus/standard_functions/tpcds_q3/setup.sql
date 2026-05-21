-- TPC-DS Q3 setup — 3-table star schema slice (date_dim ⋈ store_sales ⋈ item).
-- Minimal SF-tiny data tuned to TPC-DS spec Q3 validation parameters
-- (MANUFACT=128, MONTH=11): at least one matching row per d_year so the
-- recorded baseline returns multiple grouping keys.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_moy INT64,
  d_dow INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451850, DATE "2000-11-01", 2000, 11, 3, 4),
  (2451851, DATE "2000-11-02", 2000, 11, 4, 4),
  (2451852, DATE "2000-11-03", 2000, 11, 5, 4),
  (2451853, DATE "2000-11-04", 2000, 11, 6, 4),
  (2452215, DATE "2001-11-01", 2001, 11, 4, 4),
  (2452216, DATE "2001-11-02", 2001, 11, 5, 4),
  (2452580, DATE "2002-11-01", 2002, 11, 5, 4),
  (2452581, DATE "2002-11-02", 2002, 11, 6, 4),
  (2451820, DATE "2000-10-02", 2000, 10, 1, 4);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING,
  i_current_price NUMERIC, i_wholesale_cost NUMERIC,
  i_brand_id INT64, i_brand STRING,
  i_class_id INT64, i_class STRING,
  i_category_id INT64, i_category STRING,
  i_manufact_id INT64, i_manufact STRING,
  i_manager_id INT64, i_product_name STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1", "Item 1 desc",  NUMERIC "10.00", NUMERIC "5.00", 1001, "branda #1", 1, "class-1", 1, "Sports",   128, "manuf-128", 1, "PROD-1"),
  (2, "AAAA2", "Item 2 desc",  NUMERIC "20.00", NUMERIC "9.00", 1002, "brandb #2", 1, "class-1", 1, "Sports",   128, "manuf-128", 1, "PROD-2"),
  (3, "AAAA3", "Item 3 desc",  NUMERIC "15.00", NUMERIC "7.00", 1003, "brandc #3", 2, "class-2", 2, "Music",    128, "manuf-128", 1, "PROD-3"),
  (4, "AAAA4", "Item 4 desc",  NUMERIC "30.00", NUMERIC "12.0", 1004, "brandd #4", 2, "class-2", 2, "Music",    200, "manuf-200", 2, "PROD-4"),
  (5, "AAAA5", "Item 5 desc",  NUMERIC "12.00", NUMERIC "6.00", 1005, "brande #5", 3, "class-3", 3, "Books",    128, "manuf-128", 2, "PROD-5"),
  (6, "AAAA6", "Item 6 desc",  NUMERIC "25.00", NUMERIC "11.0", 1006, "brandf #6", 3, "class-3", 3, "Books",    300, "manuf-300", 2, "PROD-6");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64, ss_customer_sk INT64,
  ss_store_sk INT64, ss_ticket_number INT64,
  ss_quantity INT64, ss_sales_price NUMERIC,
  ss_ext_sales_price NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451850, 1, 1, 1, 1001, 3, NUMERIC "10.00", NUMERIC "30.00", NUMERIC "5.00"),
  (2451850, 2, 1, 1, 1002, 2, NUMERIC "20.00", NUMERIC "40.00", NUMERIC "10.0"),
  (2451851, 3, 2, 1, 1003, 5, NUMERIC "15.00", NUMERIC "75.00", NUMERIC "20.0"),
  (2451852, 1, 3, 1, 1004, 4, NUMERIC "10.00", NUMERIC "40.00", NUMERIC "8.00"),
  (2451853, 5, 3, 2, 1005, 1, NUMERIC "12.00", NUMERIC "12.00", NUMERIC "3.00"),
  (2452215, 1, 4, 1, 1006, 6, NUMERIC "10.00", NUMERIC "60.00", NUMERIC "12.0"),
  (2452216, 3, 4, 2, 1007, 2, NUMERIC "15.00", NUMERIC "30.00", NUMERIC "6.00"),
  (2452580, 2, 5, 1, 1008, 4, NUMERIC "20.00", NUMERIC "80.00", NUMERIC "15.0"),
  (2452581, 5, 5, 2, 1009, 3, NUMERIC "12.00", NUMERIC "36.00", NUMERIC "8.00"),
  (2451820, 4, 1, 1, 1010, 1, NUMERIC "30.00", NUMERIC "30.00", NUMERIC "6.00");
