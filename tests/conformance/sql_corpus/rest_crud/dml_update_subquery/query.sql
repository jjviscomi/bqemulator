UPDATE `${DATASET}.orders`
SET amount = amount * NUMERIC '2'
WHERE customer IN (SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 2);
SELECT order_id, amount FROM `${DATASET}.orders` ORDER BY order_id
