-- TPC-DS Q34 setup — store_sales ⋈ date_dim ⋈ store ⋈ household_demographics
-- ⋈ customer (5-table star). Spec params: hd_buy_potential IN (>10000,Unknown),
-- d_year IN (1999,2000,2001), d_dom BETWEEN 1 AND 3 OR BETWEEN 25 AND 28.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_dom INT64, d_year INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  -- 1999 d_dom=1,2,3 + 25,26,27
  (2451179, 1, 1999),
  (2451180, 2, 1999),
  (2451203, 25, 1999),
  -- 2000 d_dom=2,3 + 25,28
  (2451546, 2, 2000),
  (2451569, 25, 2000),
  -- 2001 d_dom=1,3,26
  (2451911, 1, 2001),
  (2451913, 3, 2001),
  -- Out of range
  (2451200, 22, 1999);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_county STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "Williamson County"),
  (2, "Williamson County");

CREATE OR REPLACE TABLE `${DATASET}.household_demographics` (
  hd_demo_sk INT64, hd_buy_potential STRING,
  hd_dep_count INT64, hd_vehicle_count INT64
);
INSERT INTO `${DATASET}.household_demographics` VALUES
  (1, ">10000",  2, 1),
  (2, "Unknown", 4, 1),
  (3, "501-1000", 5, 2);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_salutation STRING,
  c_first_name STRING, c_last_name STRING,
  c_preferred_cust_flag STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "Mr.", "Alice", "Anderson", "Y"),
  (2, "Ms.", "Bob",   "Brown",    "N"),
  (3, "Dr.", "Carol", "Clarke",   "Y");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_store_sk INT64, ss_hdemo_sk INT64,
  ss_ticket_number INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- customer 1 buys 16 tickets (15-20 range)
  (2451179, 1, 1, 1, 1001),
  (2451179, 1, 1, 1, 1002),
  (2451180, 1, 1, 1, 1003),
  (2451180, 1, 1, 1, 1004),
  (2451203, 1, 1, 1, 1005),
  (2451203, 1, 1, 1, 1006),
  (2451546, 1, 1, 1, 1007),
  (2451546, 1, 1, 1, 1008),
  (2451569, 1, 1, 1, 1009),
  (2451569, 1, 1, 1, 1010),
  (2451911, 1, 1, 1, 1011),
  (2451911, 1, 1, 1, 1012),
  (2451913, 1, 1, 1, 1013),
  (2451913, 1, 1, 1, 1014),
  (2451179, 1, 1, 1, 1015),
  (2451180, 1, 1, 1, 1016),
  -- customer 2 buys only 5 tickets (below 15)
  (2451179, 2, 2, 2, 1101),
  (2451180, 2, 2, 2, 1102),
  (2451203, 2, 2, 2, 1103),
  (2451546, 2, 2, 2, 1104),
  (2451569, 2, 2, 2, 1105),
  -- customer 3 buys 21 tickets (above 20)
  (2451179, 3, 1, 1, 1201),
  (2451179, 3, 1, 1, 1202),
  (2451180, 3, 1, 1, 1203),
  (2451180, 3, 1, 1, 1204),
  (2451203, 3, 1, 1, 1205),
  (2451203, 3, 1, 1, 1206),
  (2451203, 3, 1, 1, 1207),
  (2451546, 3, 1, 1, 1208),
  (2451546, 3, 1, 1, 1209),
  (2451569, 3, 1, 1, 1210),
  (2451569, 3, 1, 1, 1211),
  (2451911, 3, 1, 1, 1212),
  (2451911, 3, 1, 1, 1213),
  (2451911, 3, 1, 1, 1214),
  (2451913, 3, 1, 1, 1215),
  (2451913, 3, 1, 1, 1216),
  (2451913, 3, 1, 1, 1217),
  (2451179, 3, 1, 1, 1218),
  (2451180, 3, 1, 1, 1219),
  (2451203, 3, 1, 1, 1220),
  (2451546, 3, 1, 1, 1221);
