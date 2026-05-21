DELETE FROM `${DATASET}.orders` AS o
WHERE EXISTS (SELECT 1 FROM `${DATASET}.orders` WHERE order_id = o.order_id AND amount > NUMERIC '200');
SELECT order_id FROM `${DATASET}.orders` ORDER BY order_id
