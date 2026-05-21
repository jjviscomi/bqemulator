-- TPC-DS Q23 (Q23a variant) setup — multi-CTE pipeline with 3 WITH clauses,
-- nested IN-subqueries, and a scalar subquery in HAVING.
-- Tables: store_sales, date_dim, item, customer, catalog_sales, web_sales.
-- Spec params: d_year IN (2000..2003), d_moy=2 in the outer query, count(*) > 4 in
-- frequent_ss_items, and a 0.5 * max(csales) HAVING threshold in best_ss_customer.
-- SF-tiny data tuned so frequent_ss_items + best_ss_customer + the catalog/web
-- subqueries each have a non-empty result.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  -- 2000-2003 for the CTE filter
  (2451550, DATE "2000-02-01", 2000, 2),
  (2451551, DATE "2000-02-02", 2000, 2),
  (2451600, DATE "2000-03-01", 2000, 3),
  (2451910, DATE "2000-12-31", 2000, 12),
  (2452020, DATE "2001-04-19", 2001, 4),
  (2452200, DATE "2001-10-15", 2001, 10),
  (2452400, DATE "2002-05-03", 2002, 5),
  (2452800, DATE "2003-06-10", 2003, 6),
  -- 2004 to ensure NOT all rows match the CTE filter
  (2453200, DATE "2004-07-12", 2004, 7);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_desc STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Widget alpha extra premium edition limited"),
  (2, "Gizmo bravo regular standard run"),
  (3, "Sprocket charlie compact lightweight model"),
  (4, "Cogwheel delta heavy industrial unit"),
  (5, "Bracket echo titanium reinforced");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_customer_id STRING,
  c_first_name STRING, c_last_name STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, "AAAA1", "Alice",   "Anderson"),
  (2, "AAAA2", "Bob",     "Brown"),
  (3, "AAAA3", "Carol",   "Clarke"),
  (4, "AAAA4", "Dave",    "Davis");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_customer_sk INT64, ss_quantity INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- item 1 sold 5+ times on 2451550 → frequent_ss_items qualifies
  (2451550, 1, 1, 3, NUMERIC "10.00"),
  (2451550, 1, 1, 2, NUMERIC "10.00"),
  (2451550, 1, 2, 4, NUMERIC "10.00"),
  (2451550, 1, 3, 1, NUMERIC "10.00"),
  (2451550, 1, 4, 6, NUMERIC "10.00"),
  -- item 1 on 2451551 also 5+ → second qualifying row
  (2451551, 1, 1, 2, NUMERIC "10.00"),
  (2451551, 1, 2, 3, NUMERIC "10.00"),
  (2451551, 1, 3, 4, NUMERIC "10.00"),
  (2451551, 1, 4, 5, NUMERIC "10.00"),
  (2451551, 1, 1, 1, NUMERIC "10.00"),
  -- item 2: high-value purchase by customer 1 across years → top spender
  (2451600, 2, 1, 20, NUMERIC "50.00"),
  (2452020, 2, 1, 15, NUMERIC "50.00"),
  (2452400, 2, 1, 10, NUMERIC "50.00"),
  (2452800, 2, 2,  1, NUMERIC "10.00"),
  -- light coverage of items 3-5
  (2451550, 3, 2, 2, NUMERIC "8.00"),
  (2451600, 4, 3, 1, NUMERIC "20.00"),
  (2452020, 5, 4, 3, NUMERIC "7.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_item_sk INT64, cs_quantity INT64,
  cs_list_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- Feb 2000 (2451550, 2451551), item 1 (frequent), customer 1 (best) →
  -- qualifies through both IN-subqueries
  (2451550, 1, 1, 4, NUMERIC "10.00"),
  (2451551, 1, 1, 2, NUMERIC "10.00"),
  -- Feb 2000, item 1, non-best customer (3) → filtered by best_ss_customer
  (2451550, 1, 3, 1, NUMERIC "10.00"),
  -- Feb 2000, item 4 (non-frequent), customer 1 (best) → filtered by frequent_ss_items
  (2451550, 4, 1, 1, NUMERIC "20.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_bill_customer_sk INT64,
  ws_item_sk INT64, ws_quantity INT64,
  ws_list_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  -- Feb 2000, item 1, customer 1 → qualifies
  (2451551, 1, 1, 3, NUMERIC "10.00"),
  -- Feb 2000, item 2 (non-frequent), customer 1 → filtered by frequent_ss_items
  (2451550, 2, 1, 1, NUMERIC "50.00");
