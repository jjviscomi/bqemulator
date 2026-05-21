-- TPC-DS Q84 setup — 5-table customer-demographics star with **income_band**
-- table (new TPC-DS table in corpus). Spec params: ca_city='Edgewood',
-- ib_lower_bound>=N, store_returns lookup.

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING,
  c_first_name STRING, c_last_name STRING,
  c_current_cdemo_sk INT64, c_current_hdemo_sk INT64,
  c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1", "Alice", "Anderson", 1, 1, 1),
  (2, "AAAA2", "Bob",   "Brown",    2, 2, 2),
  (3, "AAAA3", "Carol", "Clarke",   1, 1, 3);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_city STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "Edgewood"),
  (2, "Edgewood"),
  (3, "Other City");

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1), (2);

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_income_band_sk INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, 1),
  (2, 5);

CREATE OR REPLACE TABLE `${DATASET}.income_band` (
  ib_income_band_sk INT64, ib_lower_bound INT64, ib_upper_bound INT64
);
INSERT INTO `${DATASET}.income_band` VALUES
  (1, 38128, 40000),
  (2, 40000, 60000),
  (3, 60000, 80000),
  (4, 80000, 120000),
  (5, 120000, 150000),
  (6, 150000, 200000);

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_cdemo_sk INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (1), (1), (2);
