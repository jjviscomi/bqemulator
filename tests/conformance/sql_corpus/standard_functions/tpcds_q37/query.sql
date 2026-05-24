SELECT
  i_item_id,
  i_item_desc,
  i_current_price
FROM `${DATASET}.item` AS item,
     `${DATASET}.inventory` AS inventory,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.catalog_sales` AS catalog_sales
WHERE i_current_price BETWEEN 68 AND 98
  AND inv_item_sk = i_item_sk
  AND d_date_sk = inv_date_sk
  AND d_date BETWEEN DATE "2000-02-01"
                 AND DATE_ADD(DATE "2000-02-01", INTERVAL 60 DAY)
  AND i_manufact_id IN (677, 940, 694, 808)
  AND inv_quantity_on_hand BETWEEN 100 AND 500
  AND cs_item_sk = i_item_sk
GROUP BY i_item_id, i_item_desc, i_current_price
ORDER BY i_item_id
LIMIT 100
