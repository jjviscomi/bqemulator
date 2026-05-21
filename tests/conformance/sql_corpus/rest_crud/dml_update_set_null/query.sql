UPDATE `${DATASET}.orders` SET customer = NULL WHERE order_id = 1;
SELECT customer FROM `${DATASET}.orders` WHERE order_id = 1
