-- TPC-DS Q99 setup — catalog sales day-bucket pivot by sm_type + cc_name
-- + warehouse + d_year + d_quarter + d_moy. Spec params: d_month_seq=1200..1211.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64, d_year INT64, d_qoy INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200, 1999, 1, 1),
  (2451209, 1201, 1999, 1, 2),
  (2451240, 1202, 1999, 1, 3),
  (2451300, 1204, 1999, 2, 5);

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64, w_warehouse_name STRING
);
INSERT INTO `${DATASET}.warehouse` VALUES
  (1, "Warehouse Alpha"),
  (2, "Warehouse Beta");

CREATE OR REPLACE TABLE `${DATASET}.ship_mode` (
  sm_ship_mode_sk INT64, sm_type STRING
);
INSERT INTO `${DATASET}.ship_mode` VALUES
  (1, "EXPRESS"),
  (2, "OVERNIGHT");

CREATE OR REPLACE TABLE `${DATASET}.call_center` (
  cc_call_center_sk INT64, cc_name STRING
);
INSERT INTO `${DATASET}.call_center` VALUES
  (1, "NY Metro"),
  (2, "Mid Atlantic");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_ship_date_sk INT64,
  cs_warehouse_sk INT64, cs_ship_mode_sk INT64,
  cs_call_center_sk INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- diff = 30 days
  (2451179, 2451209, 1, 1, 1),
  -- diff = 60 days
  (2451179, 2451240, 1, 1, 1),
  -- diff = 91 days
  (2451209, 2451300, 1, 2, 1),
  -- diff = 31 days
  (2451179, 2451209, 2, 2, 2),
  -- diff = 61 days
  (2451179, 2451240, 2, 1, 1),
  -- > 90 days
  (2451179, 2451300, 1, 1, 2);
