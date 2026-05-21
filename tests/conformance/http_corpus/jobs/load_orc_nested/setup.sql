CREATE TABLE `${DATASET}.people` (
  id INT64,
  name STRING,
  addr STRUCT<city STRING, zip STRING>
);
