CREATE OR REPLACE TABLE `${DATASET}.orders` (order_id INT64, amount NUMERIC);
INSERT INTO `${DATASET}.orders` (order_id, amount) VALUES
  (1, NUMERIC "10.00"), (2, NUMERIC "20.00"), (3, NUMERIC "30.00");
