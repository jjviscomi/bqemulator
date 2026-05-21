SELECT order_id, customer,
       LAG(amount) OVER (PARTITION BY customer ORDER BY order_id) AS prev_amount
FROM `${DATASET}.orders`
ORDER BY order_id
