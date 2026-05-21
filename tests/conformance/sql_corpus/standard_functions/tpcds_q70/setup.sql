-- TPC-DS Q70 setup — GROUP BY ROLLUP(s_state, s_county) + RANK OVER PARTITION
-- BY GROUPING(s_state)+GROUPING(s_county). Spec params: d_month_seq=12-month
-- range; top 5 states by sum(ss_net_profit).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_month_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1200),
  (2451209, 1201),
  (2451240, 1202),
  (2451270, 1203);

CREATE OR REPLACE TABLE `${DATASET}.store` (
  s_store_sk INT64, s_state STRING, s_county STRING
);
INSERT INTO `${DATASET}.store` VALUES
  (1, "TN", "Williamson County"),
  (2, "TN", "Franklin Parish"),
  (3, "CA", "Orange County"),
  (4, "CA", "Bronx County");

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_store_sk INT64,
  ss_net_profit NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  (2451179, 1, NUMERIC "100.00"),
  (2451209, 1, NUMERIC  "80.00"),
  (2451240, 2, NUMERIC  "60.00"),
  (2451270, 2, NUMERIC  "70.00"),
  (2451179, 3, NUMERIC  "50.00"),
  (2451209, 4, NUMERIC  "40.00");
