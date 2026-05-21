UPDATE `${DATASET}.orders` AS o
SET amount = NUMERIC '0' FROM (SELECT order_id FROM `${DATASET}.orders` WHERE amount > NUMERIC '200') AS s
WHERE s.order_id = o.order_id;
SELECT order_id, amount FROM `${DATASET}.orders` ORDER BY order_id
