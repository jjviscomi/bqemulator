SELECT region, MIN_BY(product, revenue) AS bottom_product
FROM `${DATASET}.sales`
GROUP BY region
ORDER BY region
