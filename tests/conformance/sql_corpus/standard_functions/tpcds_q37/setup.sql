-- TPC-DS Q37 setup — item ⋈ inventory ⋈ date_dim + EXISTS-via-join to
-- catalog_sales. Filters: i_current_price between 68 and 98; i_manufact_id IN
-- (677,940,694,808); inv_quantity_on_hand between 100 and 500; d_date in 60-day
-- window starting 2000-02-01.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451576, DATE "2000-02-01"),
  (2451590, DATE "2000-02-15"),
  (2451605, DATE "2000-03-01"),
  (2451620, DATE "2000-03-16"),
  (2451700, DATE "2000-06-04"); -- out of 60-day window

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING,
  i_current_price NUMERIC, i_manufact_id INT64
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "ITEM001", "Item one description", NUMERIC "70.00", 677),
  (2, "ITEM002", "Item two description", NUMERIC "85.00", 940),
  (3, "ITEM003", "Item three description",NUMERIC "97.00", 694),
  (4, "ITEM004", "Item four description", NUMERIC "75.00", 808),
  -- excluded: price out of range
  (5, "ITEM005", "Item five (too cheap)", NUMERIC "10.00", 677),
  -- excluded: manufact not in list
  (6, "ITEM006", "Item six (bad mfg)",    NUMERIC "80.00", 999);

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  (2451576, 1, 250),
  (2451590, 2, 300),
  (2451605, 3, 400),
  (2451620, 4, 150),
  -- excluded: out of qty range
  (2451576, 1,  50),
  (2451576, 1, 700),
  -- excluded: out of date window
  (2451700, 1, 200),
  -- excluded item from inventory perspective
  (2451576, 5, 200),
  (2451576, 6, 200);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_item_sk INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (1), (2), (3), (4),
  -- catalog_sales row for excluded item 5 (price-excluded — irrelevant)
  (5);
