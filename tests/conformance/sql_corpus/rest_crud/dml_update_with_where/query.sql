UPDATE `${DATASET}.orders` SET amount = NUMERIC '0.00' WHERE order_id = 3;
SELECT amount FROM `${DATASET}.orders` WHERE order_id = 3
