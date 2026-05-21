SELECT
  item.i_brand_id AS brand_id,
  item.i_brand AS brand,
  SUM(ss_ext_sales_price) AS ext_price
FROM `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.store_sales` AS store_sales,
     `${DATASET}.item` AS item
WHERE d_date_sk = ss_sold_date_sk
  AND ss_item_sk = i_item_sk
  AND i_manager_id = 28
  AND d_moy = 11
  AND d_year = 2000
GROUP BY item.i_brand, item.i_brand_id
ORDER BY ext_price DESC, item.i_brand_id
LIMIT 100
