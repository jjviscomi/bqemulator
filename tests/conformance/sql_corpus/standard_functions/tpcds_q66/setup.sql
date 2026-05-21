-- TPC-DS Q66 setup — Multi-quarter pivot across catalog+web channels with
-- warehouse + ship_mode tables. Spec params: warehouse-by-quarter SUM(CASE
-- WHEN d_moy=...) plus warehouse-by-month for sales totals.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452276, 2001, 1),
  (2452308, 2001, 2),
  (2452337, 2001, 3),
  (2452367, 2001, 4),
  (2452398, 2001, 5),
  (2452428, 2001, 6),
  (2452459, 2001, 7),
  (2452490, 2001, 8),
  (2452520, 2001, 9),
  (2452551, 2001, 10),
  (2452581, 2001, 11),
  (2452612, 2001, 12),
  (2451911, 2000, 1);

CREATE OR REPLACE TABLE `${DATASET}.time_dim` (
  t_time_sk INT64, t_time INT64
);
INSERT INTO `${DATASET}.time_dim` VALUES
  (30001, 30001),
  (40000, 40000),
  (50000, 50000),
  (10000, 10000);

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64, w_warehouse_name STRING,
  w_warehouse_sq_ft INT64, w_city STRING, w_county STRING,
  w_state STRING, w_country STRING
);
INSERT INTO `${DATASET}.warehouse` VALUES
  (1, "Warehouse Alpha", 100000, "Memphis", "Shelby",  "TN", "United States"),
  (2, "Warehouse Beta",  200000, "Nashville", "Davidson", "TN", "United States");

CREATE OR REPLACE TABLE `${DATASET}.ship_mode` (
  sm_ship_mode_sk INT64, sm_carrier STRING, sm_type STRING
);
INSERT INTO `${DATASET}.ship_mode` VALUES
  (1, "DHL",   "EXPRESS"),
  (2, "BARIAN", "OVERNIGHT");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_sold_time_sk INT64,
  ws_ship_mode_sk INT64, ws_warehouse_sk INT64,
  ws_quantity INT64, ws_sales_price NUMERIC, ws_net_paid NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2452276, 30001, 1, 1, 3, NUMERIC "100.00", NUMERIC "95.00"),
  (2452308, 30001, 1, 1, 2, NUMERIC "120.00", NUMERIC "115.00"),
  (2452337, 30001, 1, 2, 4, NUMERIC  "80.00", NUMERIC  "76.00"),
  (2452459, 40000, 1, 1, 1, NUMERIC  "50.00", NUMERIC  "48.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_sold_time_sk INT64,
  cs_ship_mode_sk INT64, cs_warehouse_sk INT64,
  cs_quantity INT64, cs_sales_price NUMERIC, cs_net_paid NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2452276, 30001, 1, 1, 2, NUMERIC  "70.00", NUMERIC  "66.50"),
  (2452459, 40000, 1, 2, 3, NUMERIC  "90.00", NUMERIC  "85.50");
