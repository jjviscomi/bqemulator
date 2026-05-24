-- TPC-DS Q81 setup — customer_total_return CTE (store_returns by state)
-- joined back to customer + customer_address; > 1.2 * AVG correlated
-- subquery per state. Spec params: d_year=2000, ca_state='GA'.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451545, 2000),
  (2451550, 2000),
  (2451910, 2000),
  (2451911, 2001); -- excluded (wrong year)

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_state STRING,
  ca_street_number STRING, ca_street_name STRING,
  ca_city STRING, ca_zip STRING,
  ca_country STRING, ca_gmt_offset NUMERIC, ca_location_type STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "GA", "100", "Peachtree St",    "Atlanta",  "30303", "US", NUMERIC "-5.00", "single"),
  (2, "GA", "200", "Forsyth Ave",     "Macon",    "31201", "US", NUMERIC "-5.00", "single"),
  (3, "GA", "300", "Riverside Dr",    "Augusta",  "30901", "US", NUMERIC "-5.00", "single"),
  (4, "TN", "400", "Broadway",        "Nashville","37203", "US", NUMERIC "-6.00", "single"); -- excluded by state

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING,
  c_current_addr_sk INT64,
  c_salutation STRING, c_first_name STRING, c_last_name STRING,
  c_preferred_cust_flag STRING, c_birth_day INT64, c_birth_month INT64,
  c_birth_year INT64, c_birth_country STRING,
  c_login STRING, c_email_address STRING, c_last_review_date STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1", 1, "Mr.",   "Adam",     "Adams",   "Y",  1,  1, 1980, "USA", "alogin", "a@example.com", "2000-02-01"),
  (2, "AAAA2", 2, "Mrs.",  "Brenda",   "Brown",   "Y",  2,  2, 1981, "USA", "blogin", "b@example.com", "2000-02-01"),
  (3, "AAAA3", 3, "Dr.",   "Carla",    "Carter",  "N",  3,  3, 1982, "USA", "clogin", "c@example.com", "2000-02-01"),
  (4, "AAAA4", 4, "Mr.",   "Dan",      "Davis",   "N",  4,  4, 1983, "USA", "dlogin", "d@example.com", "2000-02-01");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_customer_sk INT64, sr_addr_sk INT64,
  sr_return_amt_inc_tax NUMERIC
);
INSERT INTO `${DATASET}.store_returns` VALUES
  -- GA state: customer 1 spikes (passes > 1.2 * AVG_GA)
  (2451545, 1, 1, NUMERIC "1000.00"),
  (2451550, 2, 2, NUMERIC  "100.00"),
  (2451910, 3, 3, NUMERIC  "200.00"),
  -- TN state: customer 4 (excluded by state filter on outer query)
  (2451545, 4, 4, NUMERIC "9999.00"),
  -- wrong year (excluded)
  (2451911, 1, 1, NUMERIC "9999.00");
