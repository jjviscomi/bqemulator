SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 5
EXCEPT DISTINCT
SELECT customer FROM `${DATASET}.orders` WHERE order_id <= 1
ORDER BY customer
