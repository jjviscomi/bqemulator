-- TPC-DS Q22 setup — GROUP BY ROLLUP(i_product_name, i_brand, i_class,
-- i_category) over inventory + item + date_dim + warehouse. Spec params:
-- d_month_seq 1200..1211.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200),
  (2451209, 1201),
  (2451240, 1202),
  (2451550, 1212);

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64
);
INSERT INTO `${DATASET}.warehouse` VALUES (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_product_name STRING,
  i_brand STRING, i_class STRING, i_category STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Widget Alpha",     "BrandA", "athletic", "Sports"),
  (2, "Widget Bravo",     "BrandA", "athletic", "Sports"),
  (3, "Gizmo Charlie",    "BrandB", "team",     "Sports"),
  (4, "Sprocket Delta",   "BrandC", "rock",     "Music");

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64,
  inv_warehouse_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  (2451179, 1, 1, 100),
  (2451179, 2, 1,  80),
  (2451209, 3, 2,  60),
  (2451240, 4, 1,  50),
  (2451179, 1, 2, 110),
  (2451209, 2, 1,  70);
