SELECT order_id,
       DENSE_RANK() OVER (ORDER BY customer) AS r
FROM `${DATASET}.orders`
ORDER BY order_id
