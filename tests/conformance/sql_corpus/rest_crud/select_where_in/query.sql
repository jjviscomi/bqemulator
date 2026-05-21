SELECT order_id, customer FROM `${DATASET}.orders`
WHERE customer IN ('Alice', 'Carol') ORDER BY order_id
