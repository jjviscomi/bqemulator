SELECT customer, COUNT(*) AS n
FROM `${DATASET}.orders`
GROUP BY customer HAVING COUNT(*) >= 2
ORDER BY customer
