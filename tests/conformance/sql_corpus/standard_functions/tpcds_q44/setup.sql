-- TPC-DS Q44 setup — window functions on aggregated subqueries to identify
-- top/bottom-revenue items for a given store. Spec params: s_store_sk=4.

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64,
  i_product_name STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Widget Alpha"),
  (2, "Gizmo Bravo"),
  (3, "Sprocket Charlie"),
  (4, "Cogwheel Delta"),
  (5, "Bracket Echo"),
  (6, "Frame Foxtrot");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_store_sk INT64, ss_item_sk INT64,
  ss_addr_sk INT64, ss_cdemo_sk INT64,
  ss_net_profit NUMERIC, ss_hdemo_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- store 4 sales
  (4, 1, 1, 1, NUMERIC  "10.00", 1),
  (4, 2, 1, 1, NUMERIC  "50.00", 1),
  (4, 3, 1, 1, NUMERIC  "30.00", 1),
  (4, 4, 1, 1, NUMERIC  "70.00", 1),
  (4, 5, 1, 1, NUMERIC  "20.00", 1),
  (4, 6, 1, 1, NUMERIC  "40.00", 1),
  -- other store
  (5, 1, 2, 2, NUMERIC "100.00", 2);
