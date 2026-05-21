DELETE FROM `${DATASET}.orders` WHERE order_id > 1000;
SELECT COUNT(*) AS remaining FROM `${DATASET}.orders`
