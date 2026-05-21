SELECT order_id,
       SUM(amount) OVER (ORDER BY order_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM `${DATASET}.orders`
ORDER BY order_id
