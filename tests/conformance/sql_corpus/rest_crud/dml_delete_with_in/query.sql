DELETE FROM `${DATASET}.orders` WHERE order_id IN (1, 2);
SELECT order_id FROM `${DATASET}.orders` ORDER BY order_id
