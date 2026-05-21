SELECT
  i_item_id,
  s_state,
  GROUPING(s_state) AS g_state,
  AVG(ss_quantity) AS agg1,
  AVG(ss_list_price) AS agg2,
  AVG(ss_coupon_amt) AS agg3,
  AVG(ss_sales_price) AS agg4
FROM `${DATASET}.store_sales` AS store_sales,
     `${DATASET}.customer_demographics` AS customer_demographics,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.store` AS store,
     `${DATASET}.item` AS item
WHERE ss_sold_date_sk = d_date_sk
  AND ss_item_sk = i_item_sk
  AND ss_store_sk = s_store_sk
  AND ss_cdemo_sk = cd_demo_sk
  AND cd_gender = 'F'
  AND cd_marital_status = 'W'
  AND cd_education_status = 'Primary'
  AND d_year = 1998
  AND s_state IN ('TN', 'TN', 'TN', 'TN', 'TN', 'TN')
GROUP BY ROLLUP (i_item_id, s_state)
ORDER BY i_item_id, s_state
LIMIT 100
