SELECT customer, COUNT(*) AS n
FROM `${DATASET}.orders`
GROUP BY customer
ORDER BY customer
