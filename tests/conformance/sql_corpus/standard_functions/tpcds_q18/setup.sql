-- TPC-DS Q18 setup — GROUP BY ROLLUP(i_item_id, ca_country, ca_state,
-- ca_county) — 4-level ROLLUP. Spec params: d_year=1998, cd_gender='F',
-- cd_education_status='Unknown', cd_dep_count IN (1,3,9,12,15),
-- cd_dep_employed_count IN (1,3,9,12,15), AGE BETWEEN 25 AND 40.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2450816, 1998),
  (2450817, 1998),
  (2450900, 1998),
  (2451180, 1999);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_birth_month INT64, c_birth_year INT64,
  c_current_cdemo_sk INT64, c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 6, 1970, 1, 1),
  (2, 8, 1975, 1, 2),
  (3, 2, 1965, 2, 3);

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_gender STRING,
  cd_education_status STRING,
  cd_dep_count INT64, cd_dep_employed_count INT64
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "F", "Unknown",  1, 1),
  (2, "M", "College",  2, 2),
  (3, "F", "Unknown",  9, 12);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_country STRING,
  ca_state STRING, ca_county STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "United States", "TN", "Williamson County"),
  (2, "United States", "CA", "Orange County"),
  (3, "United States", "TN", "Franklin Parish");

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1"),
  (2, "AAAA2"),
  (3, "AAAA3");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_bill_cdemo_sk INT64, cs_bill_addr_sk INT64,
  cs_item_sk INT64, cs_quantity INT64,
  cs_list_price NUMERIC, cs_coupon_amt NUMERIC,
  cs_sales_price NUMERIC, cs_net_profit NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2450816, 1, 1, 1, 1,  3, NUMERIC "10.00", NUMERIC "0.50", NUMERIC  "9.50", NUMERIC "2.00"),
  (2450817, 1, 1, 1, 2,  5, NUMERIC "15.00", NUMERIC "1.00", NUMERIC "14.00", NUMERIC "3.00"),
  (2450900, 2, 1, 2, 1,  2, NUMERIC "12.00", NUMERIC "0.30", NUMERIC "11.70", NUMERIC "2.50"),
  (2450816, 3, 3, 3, 3,  1, NUMERIC "20.00", NUMERIC "0.00", NUMERIC "20.00", NUMERIC "4.00");
