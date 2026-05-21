-- TPC-DS Q6 setup — Customer + state + item.i_current_price > 1.2 * AVG over
-- same i_category from a correlated subquery. Spec params: d_year=2001,
-- d_moy=1, HAVING COUNT(*) > 10.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2452276, 2001, 1, 1212),
  (2452277, 2001, 1, 1212),
  (2452641, 2002, 1, 1224);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_current_price NUMERIC, i_category STRING
);
INSERT INTO `${DATASET}.item` VALUES
  -- Category Books: items 1-4 with varying prices; AVG=50, 1.2x = 60
  (1, NUMERIC "40.00",  "Books"),
  (2, NUMERIC "60.00",  "Books"),
  (3, NUMERIC "50.00",  "Books"),
  (4, NUMERIC "100.00", "Books"),
  -- Category Music
  (5, NUMERIC "30.00",  "Music");

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_state STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "TN"),
  (2, "CA"),
  (3, "NY");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1),
  (2, 1),
  (3, 1),
  (4, 1),
  (5, 1),
  (6, 1),
  (7, 1),
  (8, 1),
  (9, 1),
  (10, 1),
  (11, 1),
  (12, 1),
  (13, 2);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_customer_sk INT64, ss_sold_date_sk INT64, ss_item_sk INT64
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- 12 customers in TN buying item 4 (price > 1.2 * AVG of Books)
  (1, 2452276, 4),
  (2, 2452276, 4),
  (3, 2452276, 4),
  (4, 2452276, 4),
  (5, 2452276, 4),
  (6, 2452276, 4),
  (7, 2452276, 4),
  (8, 2452276, 4),
  (9, 2452276, 4),
  (10, 2452276, 4),
  (11, 2452276, 4),
  (12, 2452276, 4),
  -- 1 customer in CA buying item 4 (only 1, won't satisfy HAVING > 10)
  (13, 2452276, 4);
