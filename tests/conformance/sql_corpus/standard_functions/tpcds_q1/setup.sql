-- TPC-DS Q1 setup — customer_total_return CTE + `> 1.2 * AVG()` correlated
-- aggregate-threshold filter through store join with s_state='TN'. Spec params:
-- d_year=2000, s_state='TN'.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451545, 2000),
  (2451550, 2000),
  (2451910, 2000),
  (2451911, 2001);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_state STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "TN"),
  (2, "TN"),
  (3, "CA");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1"),
  (2, "AAAA2"),
  (3, "AAAA3"),
  (4, "AAAA4");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_customer_sk INT64,
  sr_store_sk INT64, sr_return_amt NUMERIC
);
INSERT INTO `${DATASET}.store_returns` VALUES
  -- store 1: customer 1 returns way above the store-1 AVG
  (2451545, 1, 1, NUMERIC "500.00"),
  (2451550, 2, 1, NUMERIC "50.00"),
  (2451910, 3, 1, NUMERIC "30.00"),
  -- store 2: customer 4 returns above store-2 AVG
  (2451545, 4, 2, NUMERIC "400.00"),
  (2451550, 2, 2, NUMERIC "100.00"),
  -- 2001 (out of d_year filter)
  (2451911, 1, 1, NUMERIC "999.00"),
  -- store 3: CA (excluded by state filter)
  (2451545, 1, 3, NUMERIC "999.00");
