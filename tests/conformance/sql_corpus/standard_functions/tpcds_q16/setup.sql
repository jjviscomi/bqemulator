-- TPC-DS Q16 setup — catalog_sales with EXISTS-self-join (different warehouse)
-- + NOT IN (catalog_returns) anti-join + call_center join.
-- Spec params: 60-day window from 2002-02-01, ca_state='GA',
-- cc_county IN ('Williamson County'...).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452306, DATE "2002-02-01"),
  (2452310, DATE "2002-02-05"),
  (2452320, DATE "2002-02-15"),
  (2452355, DATE "2002-03-22"),
  (2452365, DATE "2002-04-01");

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_state STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "GA"),
  (2, "GA"),
  (3, "CA");

CREATE OR REPLACE TABLE `${DATASET}.call_center` (
  cc_call_center_sk INT64, cc_county STRING
);
INSERT INTO `${DATASET}.call_center` VALUES
  (1, "Williamson County"),
  (2, "Franklin Parish"),
  (3, "Outside County");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_ship_date_sk INT64, cs_ship_addr_sk INT64,
  cs_call_center_sk INT64, cs_warehouse_sk INT64,
  cs_order_number INT64,
  cs_ext_ship_cost NUMERIC, cs_net_profit NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- order 1001: multi-warehouse, qualifies for EXISTS (different warehouse)
  (2452306, 1, 1, 1, 1001, NUMERIC "5.00", NUMERIC "10.00"),
  (2452306, 1, 1, 2, 1001, NUMERIC "3.00", NUMERIC  "8.00"),
  -- order 1002: multi-warehouse, GA state, qualifies
  (2452310, 2, 1, 1, 1002, NUMERIC "4.00", NUMERIC  "9.00"),
  (2452310, 2, 1, 3, 1002, NUMERIC "2.00", NUMERIC  "6.00"),
  -- order 1003: single warehouse, doesn't qualify EXISTS (warehouse same)
  (2452320, 1, 1, 1, 1003, NUMERIC "1.00", NUMERIC  "5.00"),
  -- order 1004: state CA, excluded
  (2452306, 3, 1, 1, 1004, NUMERIC "1.00", NUMERIC  "5.00"),
  -- order 1005: county not in list
  (2452310, 1, 3, 1, 1005, NUMERIC "1.00", NUMERIC  "5.00"),
  -- order 1006: in catalog_returns (NOT EXISTS will exclude)
  (2452310, 1, 1, 1, 1006, NUMERIC "1.00", NUMERIC  "5.00"),
  (2452310, 1, 1, 2, 1006, NUMERIC "1.00", NUMERIC  "5.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_order_number INT64
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  (1006);
