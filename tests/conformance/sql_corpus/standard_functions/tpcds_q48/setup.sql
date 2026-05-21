-- TPC-DS Q48 setup — 5-table star with 3 parallel multi-arm OR-disjunctions.
-- Spec params: d_year=2000, store + customer_demographics + customer_address.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451545, 2000),
  (2451550, 2000),
  (2451910, 2000),
  (2451911, 2001);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64
);
INSERT INTO `${DATASET}.store` VALUES (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_marital_status STRING,
  cd_education_status STRING
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "M", "4 yr Degree"),
  (2, "D", "2 yr Degree"),
  (3, "S", "College"),
  (4, "U", "Primary");

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_state STRING, ca_country STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "TN", "United States"),
  (2, "WI", "United States"),
  (3, "LA", "United States"),
  (4, "ON", "Canada");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64,
  ss_cdemo_sk INT64, ss_addr_sk INT64,
  ss_sales_price NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- Match arm 1: cd=M+4yr, addr=TN+US (state IN OH/NJ/IL etc — TN match), sales 100-150, profit 0-2000
  (2451545, 1, 1, 1, NUMERIC "120.00", NUMERIC "500.00"),
  -- Match arm 2: cd=D+2yr, addr=WI+US (state WI in IN/WI/MO), sales 50-100, profit 150-3000
  (2451550, 1, 2, 2, NUMERIC  "80.00", NUMERIC "800.00"),
  -- Match arm 3: cd=S+College, addr=LA+US (state LA in WA/NC/SD), sales 150-200, profit 50-25000
  (2451910, 2, 3, 3, NUMERIC "180.00", NUMERIC "1500.00"),
  -- Non-matching row (Canada, won't match country=US)
  (2451910, 2, 4, 4, NUMERIC "120.00", NUMERIC "500.00"),
  -- 2001 (year filter excludes)
  (2451911, 1, 1, 1, NUMERIC "120.00", NUMERIC "500.00");
