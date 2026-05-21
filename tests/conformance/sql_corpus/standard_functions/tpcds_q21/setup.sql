-- TPC-DS Q21 setup — inventory before/after a date_dim filter via
-- SUM(CASE WHEN d_date < ref / >= ref) ratio BETWEEN 2/3 AND 3/2.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, DATE "1999-01-01"),
  (2451180, DATE "1999-01-02"),
  (2451235, DATE "1999-02-26"),
  (2451236, DATE "1999-02-27"),
  (2451237, DATE "1999-02-28"),
  (2451300, DATE "1999-05-02"),
  (2451400, DATE "1999-08-10");

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64, w_warehouse_id STRING, w_warehouse_name STRING
);
INSERT INTO `${DATASET}.warehouse` VALUES
  (1, "AAAA1", "Warehouse Alpha"),
  (2, "AAAA2", "Warehouse Beta");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING, i_current_price NUMERIC
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "BBBB1", NUMERIC "5.00"),
  (2, "BBBB2", NUMERIC "10.00"),
  (3, "BBBB3", NUMERIC "15.00");

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64,
  inv_warehouse_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  -- Item 1 in warehouse 1: BEFORE (10) AFTER (15) — ratio 1.5, between 2/3 and 3/2
  (2451179, 1, 1, 10),
  (2451180, 1, 1, 10),
  (2451300, 1, 1, 15),
  (2451400, 1, 1, 15),
  -- Item 2 in warehouse 1: BEFORE (20) AFTER (5) — ratio 0.25, NOT in bounds
  (2451179, 2, 1, 20),
  (2451300, 2, 1,  5),
  -- Item 3 in warehouse 2: BEFORE (8) AFTER (12) — ratio 1.5, in bounds
  (2451179, 3, 2,  8),
  (2451300, 3, 2, 12);
