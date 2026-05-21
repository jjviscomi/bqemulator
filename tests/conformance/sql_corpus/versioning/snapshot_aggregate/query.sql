CREATE SNAPSHOT TABLE `${DATASET}.sales_snap`
CLONE `${DATASET}.sales`;

SELECT country, SUM(amount) AS total, COUNT(*) AS n
FROM `${DATASET}.sales_snap`
GROUP BY country
ORDER BY country
