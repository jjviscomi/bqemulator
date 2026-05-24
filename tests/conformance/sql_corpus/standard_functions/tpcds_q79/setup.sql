-- TPC-DS Q79 setup — store_sales ⋈ date_dim ⋈ store ⋈ household_demographics
-- joined to customer for display. Spec params: hd_dep_count=6 OR
-- hd_vehicle_count>2; d_dow=1; d_year IN (1999,2000,2001);
-- s_number_employees BETWEEN 200 AND 295.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_dow INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1999, 1),
  (2451209, 2000, 1),
  (2451240, 2001, 1),
  (2451270, 1999, 2),  -- wrong dow (excluded)
  (2451300, 1998, 1);  -- wrong year (excluded)

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_city STRING, s_number_employees INT64
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Midway",        250),
  (2, "Fairview",      290),
  (3, "Big Store",     500); -- out of employee range (excluded)

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_dep_count INT64, hd_vehicle_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, 6, 1),  -- passes via dep_count
  (2, 2, 4),  -- passes via vehicle_count
  (3, 1, 1);  -- excluded

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_first_name STRING, c_last_name STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "Alice",  "Anderson"),
  (2, "Bob",    "Brown"),
  (3, "Carol",  "Carter"),
  (4, "David",  "Davis");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_addr_sk INT64, ss_store_sk INT64, ss_hdemo_sk INT64,
  ss_ticket_number INT64,
  ss_coupon_amt NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- passing rows
  (2451179, 1, 100, 1, 1, 10001, NUMERIC "5.00",  NUMERIC "50.00"),
  (2451209, 2, 101, 1, 2, 10002, NUMERIC "3.00",  NUMERIC "70.00"),
  (2451240, 3, 102, 2, 1, 10003, NUMERIC "8.00",  NUMERIC "40.00"),
  (2451179, 4, 103, 2, 2, 10004, NUMERIC "2.00",  NUMERIC "90.00"),
  -- excluded by hdemo
  (2451179, 1, 100, 1, 3, 10005, NUMERIC "999.00", NUMERIC "999.00"),
  -- excluded by store (employees out of range)
  (2451179, 2, 100, 3, 1, 10006, NUMERIC "999.00", NUMERIC "999.00"),
  -- excluded by date (wrong dow)
  (2451270, 1, 100, 1, 1, 10007, NUMERIC "999.00", NUMERIC "999.00");
