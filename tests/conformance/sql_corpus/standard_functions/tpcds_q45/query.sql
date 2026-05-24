SELECT
  ca_zip,
  ca_city,
  SUM(ws_sales_price) AS sum_ws_sales_price
FROM `${DATASET}.web_sales` AS web_sales,
     `${DATASET}.customer` AS customer,
     `${DATASET}.customer_address` AS customer_address,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.item` AS item
WHERE ws_bill_customer_sk = c_customer_sk
  AND c_current_addr_sk = ca_address_sk
  AND ws_item_sk = i_item_sk
  AND (SUBSTR(ca_zip, 1, 5) IN ('85669', '86197', '73108')
       OR i_item_id IN (
         SELECT i_item_id
         FROM `${DATASET}.item` AS i2
         WHERE i2.i_item_sk IN (2, 3, 5, 7)
       ))
  AND ws_sold_date_sk = d_date_sk
  AND d_qoy = 2
  AND d_year = 2001
GROUP BY ca_zip, ca_city
ORDER BY ca_zip, ca_city
LIMIT 100
