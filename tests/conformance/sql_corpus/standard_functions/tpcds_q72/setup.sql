-- TPC-DS Q72 setup — catalog_sales ⋈ inventory ⋈ warehouse ⋈ item ⋈
-- customer_demographics ⋈ household_demographics ⋈ date_dim (d1/d2/d3) ⋈
-- promotion (LEFT OUTER) ⋈ catalog_returns (LEFT OUTER). Late-demand
-- pattern. Spec params: d_year=1999, ship-date arithmetic, inv_quantity_on_hand
-- thresholds.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_week_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, DATE "1999-01-01", 1999, 100),
  (2451180, DATE "1999-01-02", 1999, 100),
  (2451184, DATE "1999-01-06", 1999, 100),
  (2451186, DATE "1999-01-08", 1999, 101),
  (2451250, DATE "1999-03-13", 1999, 110),
  (2451300, DATE "1999-05-02", 1999, 118);

CREATE OR REPLACE TABLE `${DATASET}.warehouse` (
  w_warehouse_sk INT64, w_warehouse_name STRING
);
INSERT INTO `${DATASET}.warehouse` VALUES
  (1, "Warehouse Alpha"),
  (2, "Warehouse Beta");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_desc STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Widget Alpha"),
  (2, "Gizmo Bravo");

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_marital_status STRING, cd_education_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "D", "Primary"),
  (2, "M", "College");

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_buy_potential STRING
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, ">10000"),
  (2, "5001-10000");

CREATE OR REPLACE TABLE `${DATASET}.inventory` (
  inv_date_sk INT64, inv_item_sk INT64,
  inv_warehouse_sk INT64, inv_quantity_on_hand INT64
);
INSERT INTO `${DATASET}.inventory` VALUES
  (2451179, 1, 1, 100),
  (2451179, 2, 1,  50),
  (2451250, 1, 2,   5),
  (2451250, 2, 2,  30);

CREATE OR REPLACE TABLE `${DATASET}.promotion` (
  p_promo_sk INT64, p_promo_name STRING
);
INSERT INTO `${DATASET}.promotion` VALUES
  (1, "PromoA"),
  (2, "PromoB");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_ship_date_sk INT64,
  cs_bill_cdemo_sk INT64, cs_bill_hdemo_sk INT64,
  cs_item_sk INT64, cs_warehouse_sk INT64, cs_promo_sk INT64,
  cs_quantity INT64, cs_order_number INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451179, 2451186, 1, 1, 1, 1, 1,  3, 5001),
  (2451180, 2451184, 2, 2, 2, 1, 2,  2, 5002),
  (2451250, 2451300, 1, 1, 1, 2, 1,  1, 5003);

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_item_sk INT64, cr_order_number INT64
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (1, 5001);
