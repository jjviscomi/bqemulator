CREATE OR REPLACE TABLE `${DATASET}.orders` (order_id INT64, customer STRING, amount NUMERIC);
INSERT INTO `${DATASET}.orders` (order_id, customer, amount) VALUES
  (1, "Alice", NUMERIC "100.00"), (2, "Bob", NUMERIC "200.00"),
  (3, "Carol", NUMERIC "300.00"), (4, "Dan", NUMERIC "150.00"),
  (5, "Eve", NUMERIC "250.00");
