SELECT order_id, customer,
       SUM(amount) OVER (PARTITION BY customer) AS customer_total
FROM `${DATASET}.orders`
ORDER BY order_id
