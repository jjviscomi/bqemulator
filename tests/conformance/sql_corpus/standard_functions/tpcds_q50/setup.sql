-- TPC-DS Q50 setup — day-difference SUM(CASE WHEN sr_returned_date_sk - ss_sold_date_sk <= N)
-- bucket pivot. Spec params: d_year=2001, d_moy=8.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452155, 2001, 8),
  (2452175, 2001, 8),
  (2452185, 2001, 8),
  (2452215, 2001, 9),
  (2452245, 2001, 10),
  (2452275, 2001, 11);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_id STRING, s_store_name STRING,
  s_company_id INT64, s_street_number STRING, s_street_name STRING,
  s_street_type STRING, s_suite_number STRING, s_city STRING,
  s_county STRING, s_state STRING, s_zip STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "S001", "Store Alpha", 10, "100", "Main",  "St", "Suite A", "Memphis",   "Shelby", "TN", "37013"),
  (2, "S002", "Store Beta",  10, "200", "Elm",   "Ave","Suite B", "Nashville", "Davidson", "TN", "37020");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64,
  ss_item_sk INT64, ss_store_sk INT64,
  ss_ticket_number INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2452155, 1, 1, 1, 1001),
  (2452155, 2, 2, 1, 1002),
  (2452175, 1, 1, 1, 1003),
  (2452185, 3, 1, 2, 1004),
  (2452155, 4, 2, 2, 1005);

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_customer_sk INT64,
  sr_item_sk INT64, sr_ticket_number INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  -- diff 0-30 days
  (2452175, 1, 1, 1001),   -- 20 days
  (2452185, 2, 2, 1002),   -- 30 days
  -- diff 31-60 days
  (2452215, 1, 1, 1003),   -- 40 days
  -- diff 61-90 days
  (2452245, 3, 1, 1004),   -- 60 days
  -- diff > 90 days
  (2452275, 4, 2, 1005);   -- 120 days
