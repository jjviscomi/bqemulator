-- TPC-DS Q83 setup — 3-way JOIN of return-channel CTEs by item_id, finding
-- items returned across all 3 channels in specific weeks. Spec params: 3
-- date ranges.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_week_seq INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451960, DATE "2000-06-30", 100),
  (2451961, DATE "2000-07-01", 100),
  (2452080, DATE "2000-09-27", 113),
  (2452081, DATE "2000-09-28", 113),
  (2452200, DATE "2000-11-17", 121),
  (2452201, DATE "2000-11-18", 121);

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_item_id STRING
);
INSERT INTO `${DATASET}.item` VALUES
  (1, "AAAA1"),
  (2, "AAAA2"),
  (3, "AAAA3");

CREATE OR REPLACE TABLE `${DATASET}.store_returns` (
  sr_returned_date_sk INT64, sr_item_sk INT64,
  sr_return_quantity INT64
);
INSERT INTO `${DATASET}.store_returns` VALUES
  -- Item 1 in all 3 channels and all 3 dates
  (2451960, 1, 2),
  (2452080, 1, 3),
  (2452200, 1, 1),
  -- Item 2 only in store
  (2451960, 2, 5),
  -- Item 3 only in catalog + web (not store)
  (2452080, 3, 1);

CREATE OR REPLACE TABLE `${DATASET}.catalog_returns` (
  cr_returned_date_sk INT64, cr_item_sk INT64,
  cr_return_quantity INT64
);
INSERT INTO `${DATASET}.catalog_returns` VALUES
  -- Item 1 also in catalog
  (2451960, 1, 1),
  (2452080, 1, 2),
  (2452200, 1, 3),
  -- Item 3 in catalog
  (2452080, 3, 1);

CREATE OR REPLACE TABLE `${DATASET}.web_returns` (
  wr_returned_date_sk INT64, wr_item_sk INT64,
  wr_return_quantity INT64
);
INSERT INTO `${DATASET}.web_returns` VALUES
  -- Item 1 in web
  (2451960, 1, 4),
  (2452080, 1, 1),
  (2452200, 1, 2),
  -- Item 3 in web (but not store, so won't appear)
  (2452200, 3, 2);
