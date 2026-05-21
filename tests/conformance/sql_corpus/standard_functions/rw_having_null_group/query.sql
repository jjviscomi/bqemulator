SELECT region, SUM(amount) AS total
FROM `${DATASET}.sales`
GROUP BY region
HAVING SUM(amount) IS NULL OR SUM(amount) = 0
ORDER BY region
