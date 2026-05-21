SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 2
UNION ALL
SELECT customer FROM `${DATASET}.orders` WHERE order_id >= 4
ORDER BY customer
