INSERT INTO `${DATASET}.orders` (order_id, customer, amount)
SELECT order_id + 100, CONCAT(customer, '-copy'), amount
FROM `${DATASET}.orders` WHERE order_id <= 2;
SELECT order_id, customer FROM `${DATASET}.orders` WHERE order_id > 100 ORDER BY order_id
