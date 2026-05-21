UPDATE `${DATASET}.orders` SET amount = amount WHERE order_id = 1;
SELECT COUNT(*) AS unchanged FROM `${DATASET}.orders` WHERE order_id = 1
