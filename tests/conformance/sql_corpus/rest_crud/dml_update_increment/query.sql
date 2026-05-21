UPDATE `${DATASET}.orders` SET amount = amount + NUMERIC '10' WHERE TRUE;
SELECT order_id, amount FROM `${DATASET}.orders` ORDER BY order_id
