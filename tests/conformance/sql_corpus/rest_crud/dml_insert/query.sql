INSERT INTO `${DATASET}.orders` (order_id, customer, amount, order_date)
VALUES (6, 'Dan', NUMERIC '500.00', DATE '2024-01-19');

SELECT order_id, customer FROM `${DATASET}.orders` WHERE customer = 'Dan'
