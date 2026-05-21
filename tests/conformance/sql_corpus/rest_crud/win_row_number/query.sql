SELECT order_id, customer,
       ROW_NUMBER() OVER (PARTITION BY customer ORDER BY order_id) AS rn
FROM `${DATASET}.orders`
ORDER BY order_id
