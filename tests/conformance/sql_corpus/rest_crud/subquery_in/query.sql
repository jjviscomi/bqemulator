SELECT order_id, customer FROM `${DATASET}.orders`
WHERE customer IN (SELECT customer FROM `${DATASET}.orders` GROUP BY customer HAVING COUNT(*) >= 2)
ORDER BY order_id
