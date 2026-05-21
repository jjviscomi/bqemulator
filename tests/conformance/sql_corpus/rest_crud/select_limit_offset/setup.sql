CREATE OR REPLACE TABLE `${DATASET}.orders` (
  order_id INT64,
  customer STRING,
  amount NUMERIC,
  order_date DATE
);

INSERT INTO `${DATASET}.orders` (order_id, customer, amount, order_date) VALUES
  (1, "Alice", NUMERIC "100.00", DATE "2024-01-15"),
  (2, "Bob",   NUMERIC "250.50", DATE "2024-01-15"),
  (3, "Alice", NUMERIC  "75.00", DATE "2024-01-16"),
  (4, "Carol", NUMERIC "300.00", DATE "2024-01-17"),
  (5, "Bob",   NUMERIC  "50.00", DATE "2024-01-18");
