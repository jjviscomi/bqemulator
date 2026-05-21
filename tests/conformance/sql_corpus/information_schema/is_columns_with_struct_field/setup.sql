CREATE OR REPLACE TABLE `${DATASET}.profiles` (
  id INT64,
  address STRUCT<city STRING, zip INT64>,
  tags ARRAY<STRING>
);
