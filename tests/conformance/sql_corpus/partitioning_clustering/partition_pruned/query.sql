SELECT id, order_date FROM `${DATASET}.partitioned_orders`
WHERE order_date >= DATE '2024-02-01'
ORDER BY id
