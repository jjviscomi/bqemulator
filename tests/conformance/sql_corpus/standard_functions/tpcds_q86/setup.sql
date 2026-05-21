-- TPC-DS Q86 setup — GROUP BY ROLLUP(i_category, i_class) over web_sales +
-- date_dim + item. Spec params: d_month_seq 1200..1211.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200),
  (2451209, 1201),
  (2451240, 1202),
  (2451270, 1203);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_category STRING, i_class STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Sports", "athletic"),
  (2, "Sports", "team"),
  (3, "Music",  "rock"),
  (4, "Music",  "jazz"),
  (5, "Books",  "fiction");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_net_paid NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  (2451179, 1, NUMERIC "100.00"),
  (2451209, 1, NUMERIC  "80.00"),
  (2451240, 2, NUMERIC  "60.00"),
  (2451270, 2, NUMERIC  "70.00"),
  (2451179, 3, NUMERIC  "50.00"),
  (2451209, 4, NUMERIC  "40.00"),
  (2451240, 5, NUMERIC  "30.00");
