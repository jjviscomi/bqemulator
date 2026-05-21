-- TPC-DS Q15 setup — 4-table star (catalog_sales ⋈ customer ⋈ customer_address ⋈ date_dim).
-- Spec params: ca_state IN ('CA','GA','WA'), c_first_sales_date_sk threshold,
-- d_qoy=2, d_year=2000. The query uses an OR pattern on ca_state vs cs_sales_price.

CREATE OR REPLACE TABLE `${DATASET}.date_dim` (
  d_date_sk INT64, d_date DATE, d_year INT64, d_moy INT64, d_qoy INT64
);
INSERT INTO `${DATASET}.date_dim` VALUES
  (2451670, DATE "2000-04-01", 2000, 4, 2),
  (2451700, DATE "2000-05-01", 2000, 5, 2),
  (2451760, DATE "2000-06-30", 2000, 6, 2),
  (2451850, DATE "2000-11-01", 2000, 11, 4),
  (2452215, DATE "2001-11-01", 2001, 11, 4);

CREATE OR REPLACE TABLE `${DATASET}.customer_address` (
  ca_address_sk INT64, ca_zip STRING, ca_state STRING
);
INSERT INTO `${DATASET}.customer_address` VALUES
  (1, "85669", "CA"),
  (2, "30067", "GA"),
  (3, "98052", "WA"),
  (4, "10001", "NY"),
  (5, "85670", "CA");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_customer_sk INT64, c_current_addr_sk INT64
);
INSERT INTO `${DATASET}.customer` VALUES
  (1, 1),
  (2, 2),
  (3, 3),
  (4, 4),
  (5, 5);

CREATE OR REPLACE TABLE `${DATASET}.catalog_sales` (
  cs_sold_date_sk INT64, cs_bill_customer_sk INT64,
  cs_sales_price NUMERIC
);
INSERT INTO `${DATASET}.catalog_sales` VALUES
  (2451670, 1, NUMERIC "100.00"),
  (2451700, 2, NUMERIC "200.00"),
  (2451760, 3, NUMERIC "300.00"),
  (2451700, 4, NUMERIC "600.00"),
  (2451670, 5, NUMERIC "50.00"),
  (2451850, 1, NUMERIC "150.00"),
  (2452215, 2, NUMERIC "75.00");
