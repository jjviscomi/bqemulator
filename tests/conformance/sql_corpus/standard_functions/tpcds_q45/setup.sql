-- TPC-DS Q45 setup — web_sales ⋈ customer ⋈ customer_address ⋈ date_dim ⋈
-- item. Filters: substr(ca_zip,1,5) IN ('85669','86197','73108') OR
-- i_item_id in (select i_item_id from item where i_item_sk in (2,3,5,7));
-- d_qoy=2 AND d_year=2001.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452029, 2001, 2),
  (2452050, 2001, 2),
  (2452100, 2001, 2),
  (2452200, 2001, 3),  -- excluded (wrong qoy)
  (2451850, 2000, 2);  -- excluded (wrong year)

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_zip STRING, ca_city STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "85669", "Phoenix"),
  (2, "86197", "Tucson"),
  (3, "73108", "Oklahoma City"),
  (4, "94016", "San Francisco"); -- not in zip list

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (2,  "ITEM_A"),
  (3,  "ITEM_B"),
  (5,  "ITEM_C"),
  (7,  "ITEM_D"),
  -- not in item-id rescue list
  (10, "ITEM_Z");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_customer_sk INT64,
  ws_item_sk INT64, ws_sales_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  -- customer 1 (zip 85669) — passes via zip
  (2452029, 1, 2,  NUMERIC "50.00"),
  (2452050, 1, 10, NUMERIC "30.00"),
  -- customer 2 (zip 86197) — passes via zip
  (2452050, 2, 3,  NUMERIC "75.00"),
  -- customer 3 (zip 73108) — passes via zip
  (2452100, 3, 5,  NUMERIC "90.00"),
  -- customer 4 (zip 94016) — passes only via item-id rescue (item 7)
  (2452029, 4, 7,  NUMERIC "40.00"),
  -- customer 4 with non-rescue item (excluded)
  (2452029, 4, 10, NUMERIC "999.00"),
  -- wrong qoy (excluded)
  (2452200, 1, 2,  NUMERIC "999.00"),
  -- wrong year (excluded)
  (2451850, 1, 2,  NUMERIC "999.00");
