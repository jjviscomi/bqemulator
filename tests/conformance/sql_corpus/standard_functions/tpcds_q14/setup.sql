-- TPC-DS Q14 (Q14a variant) setup — 6-CTE INTERSECT pipeline finding items
-- present in all three sales channels by (i_brand_id, i_class_id,
-- i_category_id). Spec params: year IN (1999, 2000, 2001).

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_year INT64, d_moy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451179, 1999, 1),
  (2451300, 1999, 5),
  (2451550, 2000, 1),
  (2451850, 2000, 11),
  (2451911, 2001, 1),
  (2452215, 2001, 11);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_brand_id INT64, i_class_id INT64, i_category_id INT64
);
INSERT INTO `${DATASET}.item` VALUES
  -- Item bucket (b=101, c=1, cat=1) — present in all 3 channels (qualifies)
  (1, 101, 1, 1),
  (2, 101, 1, 1),
  -- Item bucket (b=102, c=2, cat=1) — only in store (does not qualify)
  (3, 102, 2, 1),
  -- Item bucket (b=103, c=3, cat=2) — in store + catalog (does not qualify)
  (4, 103, 3, 2),
  -- Item bucket (b=104, c=4, cat=2) — in all 3 channels (qualifies)
  (5, 104, 4, 2);

CREATE OR REPLACE TABLE `${DATASET}.store_sales` (
  ss_sold_date_sk INT64, ss_item_sk INT64,
  ss_quantity INT64, ss_list_price NUMERIC
);
INSERT INTO `${DATASET}.store_sales` VALUES
  -- bucket A (items 1/2) sold in store
  (2451179, 1, 5, NUMERIC "10.00"),
  (2451550, 2, 3, NUMERIC "10.00"),
  -- bucket B (item 3) store-only
  (2451179, 3, 2, NUMERIC "20.00"),
  -- bucket C (item 4) store+catalog
  (2451550, 4, 1, NUMERIC "30.00"),
  -- bucket D (item 5) all 3 channels
  (2451179, 5, 4, NUMERIC "15.00"),
  (2451911, 5, 2, NUMERIC "15.00");

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_item_sk INT64,
  cs_quantity INT64, cs_list_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  -- bucket A also in catalog
  (2451300, 1, 2, NUMERIC "10.00"),
  -- bucket C (item 4) only in catalog (not in web — so does not qualify)
  (2451300, 4, 1, NUMERIC "30.00"),
  -- bucket D (item 5) in catalog
  (2451300, 5, 3, NUMERIC "15.00");

CREATE OR REPLACE TABLE `${DATASET}.web_sales` (
  ws_sold_date_sk INT64, ws_item_sk INT64,
  ws_quantity INT64, ws_list_price NUMERIC
);
INSERT INTO `${DATASET}.web_sales` VALUES
  -- bucket A also in web
  (2451300, 2, 1, NUMERIC "10.00"),
  -- bucket D in web
  (2451300, 5, 2, NUMERIC "15.00");
