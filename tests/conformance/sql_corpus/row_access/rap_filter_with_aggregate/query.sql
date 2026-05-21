SELECT country, COUNT(*) AS row_count, SUM(amount) AS total
FROM `${DATASET}.orders`
GROUP BY country
ORDER BY country
