SELECT
  COUNT(DISTINCT cs_order_number) AS order_count,
  SUM(cs_ext_ship_cost) AS total_shipping_cost,
  SUM(cs_net_profit) AS total_net_profit
FROM `${DATASET}.catalog_sales` AS cs1,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.customer_address` AS customer_address,
     `${DATASET}.call_center` AS call_center
WHERE d_date BETWEEN DATE '2002-02-01' AND DATE_ADD(DATE '2002-02-01', INTERVAL 60 DAY)
  AND cs1.cs_ship_date_sk = d_date_sk
  AND cs1.cs_ship_addr_sk = ca_address_sk
  AND ca_state = 'GA'
  AND cs1.cs_call_center_sk = cc_call_center_sk
  AND cc_county IN ('Williamson County', 'Williamson County',
                    'Williamson County', 'Williamson County',
                    'Williamson County', 'Franklin Parish')
  AND EXISTS (
    SELECT *
    FROM `${DATASET}.catalog_sales` AS cs2
    WHERE cs1.cs_order_number = cs2.cs_order_number
      AND cs1.cs_warehouse_sk <> cs2.cs_warehouse_sk
  )
  AND cs1.cs_order_number NOT IN (
    SELECT cr_order_number FROM `${DATASET}.catalog_returns`
  )
ORDER BY count(distinct cs_order_number)
LIMIT 100
