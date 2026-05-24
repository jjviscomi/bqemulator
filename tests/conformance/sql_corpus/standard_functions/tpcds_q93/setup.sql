-- TPC-DS Q93 setup — store_sales LEFT OUTER JOIN store_returns, joined to
-- reason where r_reason_desc = 'reason 28'; act_sales = (qty - return_qty) *
-- price when returns present else qty * price; SUM per customer.

CREATE OR REPLACE TABLE `${DATASET}.reason` (
  r_reason_sk INT64, r_reason_desc STRING
);
INSERT INTO `${DATASET}.reason` VALUES
  (1, "reason 28"),
  (2, "reason 35"),  -- excluded
  (3, "reason 41");  -- excluded

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_item_sk INT64, ss_ticket_number INT64,
  ss_customer_sk INT64, ss_quantity INT64,
  ss_sales_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- customer 1
  (1, 1001, 1,  5, NUMERIC "10.00"),  -- has return on this ticket
  (2, 1002, 1, 10, NUMERIC "20.00"),  -- no return
  -- customer 2
  (1, 1003, 2,  3, NUMERIC "30.00"),  -- has return with reason 28
  (1, 1004, 2,  4, NUMERIC "15.00"),  -- has return with reason 35 — outer JOIN row excluded by reason filter
  -- customer 3 — no returns at all
  (3, 1005, 3,  2, NUMERIC "50.00");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_item_sk INT64, sr_ticket_number INT64,
  sr_return_quantity INT64, sr_reason_sk INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  -- customer 1: item 1 / ticket 1001 — reason 28 (passes)
  (1, 1001, 2, 1),
  -- customer 2: item 1 / ticket 1003 — reason 28 (passes)
  (1, 1003, 1, 1),
  -- customer 2: item 1 / ticket 1004 — reason 35 (excluded)
  (1, 1004, 1, 2);
