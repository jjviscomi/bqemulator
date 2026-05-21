-- TPC-DS Q73 setup — 5-table star (store_sales ⋈ date_dim ⋈ store ⋈
-- household_demographics ⋈ customer) with correlated EXISTS-style filter
-- via aggregated subquery + outer correlated join to customer.
-- Spec params: d_year IN (1999..2001), d_dom BETWEEN 1 AND 2, hd_buy_potential
-- IN ('>10000','Unknown'), hd_vehicle_count > 0, s_county set, ticket count BETWEEN 1..5.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_dom INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, DATE "1999-01-01", 1999, 1),
  (2451180, DATE "1999-01-02", 1999, 2),
  (2451545, DATE "2000-01-01", 2000, 1),
  (2451546, DATE "2000-01-02", 2000, 2),
  (2451911, DATE "2001-01-01", 2001, 1),
  (2451912, DATE "2001-01-02", 2001, 2),
  (2452275, DATE "2002-01-01", 2002, 1);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_county STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Williamson County"),
  (2, "Williamson County"),
  (3, "Other County");

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_buy_potential STRING,
  hd_dep_count INT64, hd_vehicle_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, ">10000",   2, 1),
  (2, "Unknown",  3, 2),
  (3, "0-500",    1, 0),
  (4, ">10000",   0, 3);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING,
  c_salutation STRING, c_first_name STRING, c_last_name STRING,
  c_preferred_cust_flag STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1", "Mr.", "Alice", "Anderson", "Y"),
  (2, "AAAA2", "Ms.", "Bob",   "Brown",    "N"),
  (3, "AAAA3", "Dr.", "Carol", "Clarke",   "Y");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_store_sk INT64, ss_hdemo_sk INT64,
  ss_ticket_number INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- customer 1 buys 2 tickets, both qualify
  (2451179, 1, 1, 1, 1001),
  (2451179, 1, 1, 1, 1002),
  -- customer 2 buys 3 tickets
  (2451180, 2, 2, 2, 1003),
  (2451545, 2, 1, 2, 1004),
  (2451546, 2, 2, 2, 1005),
  -- customer 3 buys 6 tickets (out of BETWEEN 1..5 range)
  (2451911, 3, 2, 1, 1006),
  (2451911, 3, 2, 1, 1007),
  (2451912, 3, 1, 1, 1008),
  (2451912, 3, 2, 1, 1009),
  (2451179, 3, 1, 1, 1010),
  (2451180, 3, 1, 1, 1011);
