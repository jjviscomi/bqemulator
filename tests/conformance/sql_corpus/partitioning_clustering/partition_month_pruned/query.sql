SELECT id FROM `${DATASET}.monthly_sales`
WHERE sale_date >= DATE '2024-02-01' AND sale_date < DATE '2024-03-01'
ORDER BY id
