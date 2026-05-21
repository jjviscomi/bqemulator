SELECT
  i_item_desc,
  w_warehouse_name,
  d1.d_week_seq,
  SUM(CASE WHEN p_promo_sk IS NULL THEN 1 ELSE 0 END) AS no_promo,
  SUM(CASE WHEN p_promo_sk IS NOT NULL THEN 1 ELSE 0 END) AS promo,
  COUNT(*) AS total_cnt
FROM `${DATASET}.catalog_sales` AS catalog_sales
JOIN `${DATASET}.inventory` AS inventory
  ON cs_item_sk = inv_item_sk
JOIN `${DATASET}.warehouse` AS warehouse
  ON w_warehouse_sk = inv_warehouse_sk
JOIN `${DATASET}.item` AS item
  ON i_item_sk = cs_item_sk
JOIN `${DATASET}.customer_demographics` AS customer_demographics
  ON cs_bill_cdemo_sk = cd_demo_sk
JOIN `${DATASET}.household_demographics` AS household_demographics
  ON cs_bill_hdemo_sk = hd_demo_sk
JOIN `${DATASET}.date_dim` AS d1
  ON cs_sold_date_sk = d1.d_date_sk
JOIN `${DATASET}.date_dim` AS d2
  ON inv_date_sk = d2.d_date_sk
JOIN `${DATASET}.date_dim` AS d3
  ON cs_ship_date_sk = d3.d_date_sk
LEFT OUTER JOIN `${DATASET}.promotion` AS promotion
  ON cs_promo_sk = p_promo_sk
LEFT OUTER JOIN `${DATASET}.catalog_returns` AS catalog_returns
  ON cr_item_sk = cs_item_sk
 AND cr_order_number = cs_order_number
WHERE d1.d_week_seq = d2.d_week_seq
  AND inv_quantity_on_hand < cs_quantity
  AND d3.d_date > DATE_ADD(d1.d_date, INTERVAL 5 DAY)
  AND hd_buy_potential = '>10000'
  AND d1.d_year = 1999
  AND cd_marital_status = 'D'
GROUP BY i_item_desc, w_warehouse_name, d1.d_week_seq
ORDER BY total_cnt DESC, i_item_desc, w_warehouse_name, d1.d_week_seq
LIMIT 100
