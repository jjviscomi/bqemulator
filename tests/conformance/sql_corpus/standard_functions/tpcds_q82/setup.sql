-- TPC-DS Q82 setup — item ⋈ inventory ⋈ date_dim ⋈ store_sales. Similar shape
-- to Q37 but joins store_sales instead of catalog_sales. Filters:
-- i_current_price BETWEEN 62 AND 92; i_manufact_id IN (129,270,821,423);
-- inv_quantity_on_hand BETWEEN 100 AND 500; d_date in 60-day window
-- starting 2000-05-25.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451690, DATE "2000-05-25"),
  (2451710, DATE "2000-06-14"),
  (2451730, DATE "2000-07-04"),
  (2451749, DATE "2000-07-23"),
  (2451800, DATE "2000-09-12"); -- out of 60-day window

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_item_desc STRING,
  i_current_price NUMERIC, i_manufact_id INT64
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "ITEM001", "Item one description",   NUMERIC "65.00", 129),
  (2, "ITEM002", "Item two description",   NUMERIC "80.00", 270),
  (3, "ITEM003", "Item three description", NUMERIC "91.00", 821),
  (4, "ITEM004", "Item four description",  NUMERIC "72.00", 423),
  -- excluded: price out of range
  (5, "ITEM005", "Item five (too cheap)",  NUMERIC "10.00", 129),
  -- excluded: manufact not in list
  (6, "ITEM006", "Item six (bad mfg)",     NUMERIC "80.00", 999);

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  (2451690, 1, 250),
  (2451710, 2, 300),
  (2451730, 3, 400),
  (2451749, 4, 150),
  -- excluded: out of qty range
  (2451690, 1,  50),
  (2451690, 1, 700),
  -- excluded: out of date window
  (2451800, 1, 200);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_item_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (1), (2), (3), (4),
  (5); -- excluded item still in store_sales — irrelevant
