SELECT order_id,
  (SELECT SUM(amount) FROM `${DATASET}.orders`) AS total_all
FROM `${DATASET}.orders`
WHERE order_id = 1
