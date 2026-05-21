SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 3
UNION DISTINCT
SELECT customer FROM `${DATASET}.orders` WHERE order_id >= 2
ORDER BY customer
