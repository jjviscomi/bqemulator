DELETE FROM `${DATASET}.orders` WHERE customer = 'Bob';

SELECT order_id, customer FROM `${DATASET}.orders` ORDER BY order_id
