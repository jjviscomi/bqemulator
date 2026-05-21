WITH high_value AS (
  SELECT customer, SUM(amount) AS total
  FROM `${DATASET}.orders`
  GROUP BY customer
)
SELECT customer, total
FROM high_value
WHERE total > NUMERIC '150.00'
ORDER BY customer
