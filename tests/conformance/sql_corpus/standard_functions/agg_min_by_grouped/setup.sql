CREATE OR REPLACE TABLE `${DATASET}.sales` (region STRING, product STRING, revenue NUMERIC);
INSERT INTO `${DATASET}.sales` (region, product, revenue) VALUES
  ("us", "widget", NUMERIC "100.00"),
  ("us", "gadget", NUMERIC "250.00"),
  ("us", "gizmo", NUMERIC "175.00"),
  ("eu", "widget", NUMERIC "80.00"),
  ("eu", "gadget", NUMERIC "60.00"),
  ("eu", "gizmo", NUMERIC "300.00");
