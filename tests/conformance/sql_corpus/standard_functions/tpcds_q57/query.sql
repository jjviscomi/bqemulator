WITH v1 AS (
  SELECT
    i_category,
    i_brand,
    cc_name,
    d_year,
    d_moy,
    SUM(cs_sales_price) AS sum_sales,
    AVG(SUM(cs_sales_price))
      OVER (PARTITION BY i_category, i_brand, cc_name) AS avg_monthly_sales,
    RANK()
      OVER (PARTITION BY i_category, i_brand, cc_name ORDER BY d_year, d_moy) AS rn
  FROM `${DATASET}.item` AS item,
       `${DATASET}.catalog_sales` AS catalog_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.call_center` AS call_center
  WHERE cs_item_sk = i_item_sk
    AND cs_sold_date_sk = d_date_sk
    AND cc_call_center_sk = cs_call_center_sk
    AND (d_year = 1999
         OR (d_year = 1999 - 1 AND d_moy = 12)
         OR (d_year = 1999 + 1 AND d_moy = 1))
  GROUP BY i_category, i_brand, cc_name, d_year, d_moy
)
SELECT
  v1.i_category,
  v1.i_brand,
  v1.cc_name,
  v1.d_year,
  v1.d_moy,
  v1.avg_monthly_sales,
  v1.sum_sales
FROM v1
WHERE v1.d_year = 1999
  AND v1.avg_monthly_sales > 0
  AND CASE WHEN v1.avg_monthly_sales > 0
           THEN ABS(v1.sum_sales - v1.avg_monthly_sales) / v1.avg_monthly_sales
           ELSE NULL END > 0.1
ORDER BY
  v1.sum_sales - v1.avg_monthly_sales,
  v1.d_moy,
  v1.cc_name
LIMIT 100
