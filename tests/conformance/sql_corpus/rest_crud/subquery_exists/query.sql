SELECT order_id FROM `${DATASET}.orders` AS o
WHERE EXISTS (SELECT 1 FROM `${DATASET}.orders` AS p WHERE p.customer = o.customer AND p.order_id < o.order_id)
ORDER BY order_id
