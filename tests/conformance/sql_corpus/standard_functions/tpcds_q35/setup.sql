-- TPC-DS Q35 setup — Multi-CTE EXISTS pattern: customers who bought in store
-- AND (catalog OR web). Spec params: d_year=2002, d_qoy<4.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452276, 2002, 1),
  (2452400, 2002, 2),
  (2452600, 2002, 3),
  (2452700, 2002, 4),
  (2452800, 2003, 1);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_cdemo_sk INT64,
  c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1, 1),
  (2, 2, 2),
  (3, 1, 1);

CREATE OR REPLACE TABLE `${DATASET}.customer_demographics` (
  cd_demo_sk INT64, cd_gender STRING,
  cd_marital_status STRING, cd_education_status STRING,
  cd_purchase_estimate INT64, cd_credit_rating STRING,
  cd_dep_count INT64, cd_dep_employed_count INT64,
  cd_dep_college_count INT64
);
INSERT INTO `${DATASET}.customer_demographics` VALUES
  (1, "F", "M", "College",  500, "Good", 1, 1, 0),
  (2, "M", "S", "Primary",  300, "Low",  0, 0, 0);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_state STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "TN"),
  (2, "GA");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_customer_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- customer 1 + 3 buy in store (qualifies for outer EXISTS)
  (2452276, 1), (2452400, 1),
  (2452276, 3);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_ship_customer_sk INT64
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- customer 1 also buys in catalog
  (2452400, 1);

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_customer_sk INT64
);
INSERT INTO `${DATASET}.web_sales` VALUES
  -- customer 3 also buys in web
  (2452276, 3);
