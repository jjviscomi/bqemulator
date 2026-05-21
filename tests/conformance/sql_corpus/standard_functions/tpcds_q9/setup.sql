-- TPC-DS Q9 setup — 5-branch CASE WHEN with scalar subqueries on store_sales
-- by ss_quantity bucket. Each branch returns AVG-or-MAX depending on a
-- COUNT comparison.

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_quantity INT64,
  ss_ext_discount_amt NUMERIC, ss_net_paid NUMERIC,
  ss_net_paid_inc_tax NUMERIC, ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- bucket 1-20: enough rows to satisfy COUNT > N
  (2451550,  5, NUMERIC "5.00", NUMERIC "50.00", NUMERIC "55.00", NUMERIC "10.00"),
  (2451550, 10, NUMERIC "3.00", NUMERIC "80.00", NUMERIC "85.00", NUMERIC "15.00"),
  (2451550, 15, NUMERIC "4.00", NUMERIC "120.00", NUMERIC "128.00", NUMERIC "20.00"),
  -- bucket 21-40
  (2451550, 25, NUMERIC "6.00", NUMERIC "150.00", NUMERIC "160.00", NUMERIC "25.00"),
  (2451550, 35, NUMERIC "8.00", NUMERIC "200.00", NUMERIC "212.00", NUMERIC "30.00"),
  -- bucket 41-60
  (2451550, 45, NUMERIC "10.00", NUMERIC "300.00", NUMERIC "315.00", NUMERIC "40.00"),
  -- bucket 61-80
  (2451550, 65, NUMERIC "12.00", NUMERIC "400.00", NUMERIC "418.00", NUMERIC "50.00"),
  -- bucket 81-100
  (2451550, 85, NUMERIC "15.00", NUMERIC "500.00", NUMERIC "520.00", NUMERIC "60.00");

CREATE OR REPLACE TABLE `${DATASET}.reason` (
  r_reason_sk INT64, r_reason_desc STRING
);
INSERT INTO `${DATASET}.reason` VALUES
  (1, "Unknown");
