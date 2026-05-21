UPDATE `${DATASET}.orders` SET amount = NUMERIC '999.00' WHERE order_id = 5;

SELECT order_id, amount FROM `${DATASET}.orders` WHERE order_id = 5
