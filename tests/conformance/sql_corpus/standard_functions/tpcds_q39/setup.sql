-- TPC-DS Q39 setup — inventory STDDEV/MEAN ratio with self-join across two
-- months. Spec params: d_year=2001, d_moy=1 / d_moy=2.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451911, 2001, 1),
  (2451912, 2001, 1),
  (2451930, 2001, 1),
  (2451935, 2001, 2),
  (2451945, 2001, 2),
  (2451960, 2001, 2),
  (2452100, 2001, 6);

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64, w_warehouse_name STRING
);
INSERT INTO `${DATASET}.warehouse` VALUES
  (1, "Warehouse Alpha"),
  (2, "Warehouse Beta");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64
);
INSERT INTO `${DATASET}.item` VALUES (1), (2), (3);

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64,
  inv_warehouse_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  -- January (d_moy=1), high variance for item 1 in warehouse 1
  (2451911, 1, 1, 100),
  (2451912, 1, 1, 200),
  (2451930, 1, 1, 300),
  -- January low variance for item 2 in warehouse 1
  (2451911, 2, 1, 50),
  (2451912, 2, 1, 52),
  (2451930, 2, 1, 51),
  -- February (d_moy=2) high variance for item 1 in warehouse 1
  (2451935, 1, 1, 400),
  (2451945, 1, 1, 100),
  (2451960, 1, 1, 200),
  -- February for item 2
  (2451935, 2, 1, 51),
  (2451945, 2, 1, 50),
  (2451960, 2, 1, 49),
  -- warehouse 2 sparse
  (2451911, 3, 2, 80),
  (2451935, 3, 2, 75);
