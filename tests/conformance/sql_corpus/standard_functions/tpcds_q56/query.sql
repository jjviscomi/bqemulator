WITH ss AS (
  SELECT i_item_id, SUM(ss_ext_sales_price) AS total_sales
  FROM `${DATASET}.store_sales` AS store_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.customer_address` AS customer_address,
       `${DATASET}.item` AS item
  WHERE i_item_id IN (
    SELECT i_item_id
    FROM `${DATASET}.item`
    WHERE i_color IN ('slate', 'blanched', 'burnished')
  )
    AND ss_item_sk = i_item_sk
    AND ss_sold_date_sk = d_date_sk
    AND d_year = 2001
    AND d_moy = 1
    AND ss_addr_sk = ca_address_sk
    AND ca_gmt_offset = -5
  GROUP BY i_item_id
),
cs AS (
  SELECT i_item_id, SUM(cs_ext_sales_price) AS total_sales
  FROM `${DATASET}.catalog_sales` AS catalog_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.customer_address` AS customer_address,
       `${DATASET}.item` AS item
  WHERE i_item_id IN (
    SELECT i_item_id
    FROM `${DATASET}.item`
    WHERE i_color IN ('slate', 'blanched', 'burnished')
  )
    AND cs_item_sk = i_item_sk
    AND cs_sold_date_sk = d_date_sk
    AND d_year = 2001
    AND d_moy = 1
    AND cs_bill_addr_sk = ca_address_sk
    AND ca_gmt_offset = -5
  GROUP BY i_item_id
),
ws AS (
  SELECT i_item_id, SUM(ws_ext_sales_price) AS total_sales
  FROM `${DATASET}.web_sales` AS web_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.customer_address` AS customer_address,
       `${DATASET}.item` AS item
  WHERE i_item_id IN (
    SELECT i_item_id
    FROM `${DATASET}.item`
    WHERE i_color IN ('slate', 'blanched', 'burnished')
  )
    AND ws_item_sk = i_item_sk
    AND ws_sold_date_sk = d_date_sk
    AND d_year = 2001
    AND d_moy = 1
    AND ws_bill_addr_sk = ca_address_sk
    AND ca_gmt_offset = -5
  GROUP BY i_item_id
)
SELECT i_item_id, SUM(total_sales) AS total_sales
FROM (
  SELECT * FROM ss
  UNION ALL
  SELECT * FROM cs
  UNION ALL
  SELECT * FROM ws
) tmp1
GROUP BY i_item_id
ORDER BY total_sales, i_item_id
LIMIT 100
