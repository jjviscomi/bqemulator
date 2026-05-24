-- TPC-DS Q57 setup — call_center ⋈ catalog_sales ⋈ item ⋈ date_dim with
-- AVG / RANK window over (i_category, i_brand, cc_name) by (d_year, d_moy).
-- Spec params: d_year=1999 with 1998-12 and 2000-01 boundary months; final
-- filter avg_monthly_sales > 0 AND abs(sum-avg)/avg > 0.1.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  -- 1998-12 boundary
  (2451149, 1998, 12),
  -- 1999 months
  (2451179, 1999,  1),
  (2451209, 1999,  2),
  (2451240, 1999,  3),
  (2451270, 1999,  4),
  (2451515, 1999, 12),
  -- 2000-01 boundary
  (2451545, 2000,  1),
  -- outside window
  (2451600, 2000,  3);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_category STRING, i_brand STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "Books", "scholaramalgamalg #14"),
  (2, "Books", "exportiunivamalg  #9"),
  (3, "Music", "amalgimporto  #1");

CREATE OR REPLACE TABLE `${DATASET}.call_center` (
  cc_call_center_sk INT64, cc_name STRING
);
INSERT INTO `${DATASET}.call_center` VALUES
  (1, "Mid Atlantic"),
  (2, "North Midwest");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_item_sk INT64,
  cs_call_center_sk INT64, cs_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- (Books, scholaramalgamalg #14, Mid Atlantic): vary across months so
  -- avg_monthly_sales is well-defined and at least one row triggers the
  -- abs(sum-avg)/avg > 0.1 filter.
  (2451149, 1, 1, NUMERIC "100.00"),  -- 1998-12
  (2451179, 1, 1, NUMERIC "120.00"),  -- 1999-01
  (2451209, 1, 1, NUMERIC "150.00"),  -- 1999-02
  (2451240, 1, 1, NUMERIC "200.00"),  -- 1999-03 (spike)
  (2451270, 1, 1, NUMERIC "110.00"),  -- 1999-04
  (2451515, 1, 1, NUMERIC "130.00"),  -- 1999-12
  (2451545, 1, 1, NUMERIC "115.00"),  -- 2000-01
  -- (Books, exportiunivamalg, North Midwest)
  (2451179, 2, 2, NUMERIC  "50.00"),
  (2451209, 2, 2, NUMERIC  "60.00"),
  (2451240, 2, 2, NUMERIC  "55.00"),
  -- outside window (excluded by date filter)
  (2451600, 1, 1, NUMERIC "999.00");
