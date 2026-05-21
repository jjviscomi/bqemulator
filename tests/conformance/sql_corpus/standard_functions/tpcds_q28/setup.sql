-- TPC-DS Q28 setup — CROSS JOIN of 6 independent SELECT-AVG-COUNT-DISTINCT
-- aggregations over store_sales with different quantity/list_price/discount
-- range predicates per subquery.

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_quantity INT64,
  ss_list_price NUMERIC,
  ss_coupon_amt NUMERIC,
  ss_wholesale_cost NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- bucket 1: ss_quantity 0-5, list_price 11-20
  ( 1, NUMERIC "15.00", NUMERIC  "5.00", NUMERIC "10.00"),
  ( 3, NUMERIC "18.00", NUMERIC  "8.00", NUMERIC "12.00"),
  ( 5, NUMERIC "20.00", NUMERIC "10.00", NUMERIC "14.00"),
  -- bucket 2: ss_quantity 6-10, list_price 91-100
  ( 7, NUMERIC "95.00", NUMERIC "20.00", NUMERIC "50.00"),
  ( 9, NUMERIC "100.00", NUMERIC "30.00", NUMERIC "55.00"),
  -- bucket 3: ss_quantity 11-15, list_price 56-100
  (12, NUMERIC "60.00", NUMERIC "15.00", NUMERIC "30.00"),
  (14, NUMERIC "90.00", NUMERIC "22.00", NUMERIC "45.00"),
  -- bucket 4: ss_quantity 16-20, list_price 22-40
  (17, NUMERIC "30.00", NUMERIC  "5.00", NUMERIC "20.00"),
  (19, NUMERIC "40.00", NUMERIC  "8.00", NUMERIC "25.00"),
  -- bucket 5: ss_quantity 21-25, list_price 78-92
  (22, NUMERIC "85.00", NUMERIC "12.00", NUMERIC "50.00"),
  -- bucket 6: ss_quantity 26-30, list_price 96-110
  (27, NUMERIC "100.00", NUMERIC "25.00", NUMERIC "65.00");
