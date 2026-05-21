SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 3
INTERSECT DISTINCT
SELECT customer FROM `${DATASET}.orders` WHERE order_id >= 2
ORDER BY customer
