-- TPC-DS Q7 setup — 5-table star (store_sales ⋈ customer_demographics ⋈ date_dim ⋈ item ⋈ promotion).
-- Spec params: cd_gender='M', cd_marital_status='S', cd_education_status='College',
-- p_channel_email='N', p_channel_event='N', d_year=2000.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451850, DATE "2000-11-01", 2000, 11),
  (2451851, DATE "2000-11-02", 2000, 11),
  (2451910, DATE "2000-12-31", 2000, 12),
  (2452215, DATE "2001-11-01", 2001, 11);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1"),
  (2, "AAAA2"),
  (3, "AAAA3"),
  (4, "AAAA4");

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_gender STRING, cd_marital_status STRING,
  cd_education_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "M", "S", "College"),
  (2, "M", "S", "College"),
  (3, "M", "M", "College"),
  (4, "F", "S", "College"),
  (5, "M", "S", "Primary");

CREATE OR REPLACE TABLE `${DATASET}.promotion` (
  p_promo_sk INT64, p_channel_email STRING, p_channel_event STRING
);
INSERT INTO `${DATASET}.promotion` VALUES
  (1, "N", "N"),
  (2, "Y", "N"),
  (3, "N", "Y"),
  (4, "N", "N");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_cdemo_sk INT64, ss_promo_sk INT64,
  ss_quantity INT64,
  ss_list_price NUMERIC, ss_coupon_amt NUMERIC,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451850, 1, 1, 1, 3, NUMERIC "20.00", NUMERIC "1.00", NUMERIC "19.00"),
  (2451850, 2, 1, 1, 2, NUMERIC "30.00", NUMERIC "2.00", NUMERIC "28.00"),
  (2451851, 1, 2, 4, 5, NUMERIC "10.00", NUMERIC "0.50", NUMERIC "9.50"),
  (2451851, 3, 2, 1, 1, NUMERIC "40.00", NUMERIC "3.00", NUMERIC "37.00"),
  (2451910, 2, 3, 1, 4, NUMERIC "25.00", NUMERIC "1.50", NUMERIC "23.50"),
  (2452215, 1, 1, 1, 6, NUMERIC "15.00", NUMERIC "0.75", NUMERIC "14.25"),
  (2451850, 4, 4, 1, 2, NUMERIC "12.00", NUMERIC "0.00", NUMERIC "12.00"),
  (2451851, 1, 5, 1, 1, NUMERIC "50.00", NUMERIC "0.00", NUMERIC "50.00");
