CREATE OR REPLACE TABLE `${DATASET}.orders` (
  order_id INT64,
  customer STRING
);
INSERT INTO `${DATASET}.orders` (order_id, customer) VALUES
  (1, 'Alice'),
  (2, 'Bob'),
  (3, 'Alice');

CREATE OR REPLACE TABLE `${DATASET}.customers` (
  customer STRING,
  region STRING
);
INSERT INTO `${DATASET}.customers` (customer, region) VALUES
  ('Alice', 'NORTH'),
  ('Bob', 'SOUTH'),
  ('Carol', 'NORTH');
