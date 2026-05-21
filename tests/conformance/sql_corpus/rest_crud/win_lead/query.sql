SELECT order_id, customer,
       LEAD(amount) OVER (PARTITION BY customer ORDER BY order_id) AS next_amount
FROM `${DATASET}.orders`
ORDER BY order_id
