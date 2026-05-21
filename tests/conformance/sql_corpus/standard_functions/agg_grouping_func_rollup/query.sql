SELECT region, GROUPING(region) AS is_total, SUM(n) AS total
FROM `${DATASET}.events`
GROUP BY ROLLUP(region)
ORDER BY is_total, region
