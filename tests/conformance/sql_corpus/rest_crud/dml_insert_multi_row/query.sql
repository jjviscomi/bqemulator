INSERT INTO `${DATASET}.orders` (order_id, customer, amount) VALUES
  (10, 'X', NUMERIC '10.00'), (11, 'Y', NUMERIC '11.00'), (12, 'Z', NUMERIC '12.00');
SELECT order_id, customer FROM `${DATASET}.orders` WHERE order_id >= 10 ORDER BY order_id
