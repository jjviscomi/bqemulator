-- TPC-DS Q24 setup — 6-table join (ssales CTE over
-- store_sales ⋈ store_returns ⋈ store ⋈ item ⋈ customer ⋈ customer_address)
-- with window OVER (PARTITION BY c_last_name, c_first_name, s_store_name).
-- Spec params: market='8', s_zip='market filter', color='peach'.

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_ticket_number INT64, ss_item_sk INT64,
  ss_customer_sk INT64, ss_store_sk INT64,
  ss_net_paid NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (1001, 1, 1, 1, NUMERIC "100.00"),
  (1002, 2, 2, 1, NUMERIC  "75.00"),
  (1003, 1, 3, 2, NUMERIC  "50.00"),
  (1004, 3, 1, 1, NUMERIC "120.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_ticket_number INT64, sr_item_sk INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  (1001, 1),
  (1002, 2),
  (1003, 1),
  (1004, 3);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_store_name STRING,
  s_market_id INT64, s_state STRING,
  s_zip STRING, s_company_name STRING,
  s_number_employees INT64
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "ese",   8, "TN", "37013", "Unknown", 100),
  (2, "abcd",  8, "TN", "37020", "Unknown", 50);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_current_price NUMERIC,
  i_size STRING, i_color STRING,
  i_units STRING, i_manager_id INT64
);
INSERT INTO `${DATASET}.item` VALUES
  (1, NUMERIC "20.00", "small",  "peach",   "Pound", 1),
  (2, NUMERIC "30.00", "medium", "blue",    "Pound", 2),
  (3, NUMERIC "25.00", "small",  "peach",   "Pound", 3);

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_first_name STRING, c_last_name STRING,
  c_birth_country STRING, c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "Alice", "Anderson", "USA",     1),
  (2, "Bob",   "Brown",    "CANADA",  2),
  (3, "Carol", "Clarke",   "JAPAN",   3);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_country STRING,
  ca_state STRING, ca_zip STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "United States", "TN", "37013"),
  (2, "Canada",        "TN", "37020"),
  (3, "Japan",         "TN", "37013");
