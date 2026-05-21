SELECT region, MAX_BY(product, revenue) AS top_product
FROM `${DATASET}.sales`
GROUP BY region
ORDER BY region
