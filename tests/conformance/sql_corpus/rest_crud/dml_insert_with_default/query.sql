INSERT INTO `${DATASET}.orders` (order_id, customer, amount)
VALUES (50, DEFAULT, NUMERIC '0');
SELECT order_id, customer FROM `${DATASET}.orders` WHERE order_id = 50
