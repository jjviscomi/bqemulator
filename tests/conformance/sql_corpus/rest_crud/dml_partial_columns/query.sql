INSERT INTO `${DATASET}.orders` (order_id, customer) VALUES (60, 'PartialCust');
SELECT order_id, customer, amount FROM `${DATASET}.orders` WHERE order_id = 60
