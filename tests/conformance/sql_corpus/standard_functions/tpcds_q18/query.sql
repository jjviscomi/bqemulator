SELECT
  i_item_id,
  ca_country,
  ca_state,
  ca_county,
  AVG(CAST(cs_quantity AS NUMERIC)) AS agg1,
  AVG(CAST(cs_list_price AS NUMERIC)) AS agg2,
  AVG(CAST(cs_coupon_amt AS NUMERIC)) AS agg3,
  AVG(CAST(cs_sales_price AS NUMERIC)) AS agg4,
  AVG(CAST(cs_net_profit AS NUMERIC)) AS agg5,
  AVG(CAST(c_birth_year AS NUMERIC)) AS agg6,
  AVG(CAST(cd1.cd_dep_count AS NUMERIC)) AS agg7
FROM `${DATASET}.catalog_sales` AS catalog_sales,
     `${DATASET}.customer_demographics` AS cd1,
     `${DATASET}.customer_demographics` AS cd2,
     `${DATASET}.customer` AS customer,
     `${DATASET}.customer_address` AS customer_address,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.item` AS item
WHERE cs_sold_date_sk = d_date_sk
  AND cs_item_sk = i_item_sk
  AND cs_bill_cdemo_sk = cd1.cd_demo_sk
  AND cs_bill_customer_sk = c_customer_sk
  AND cd1.cd_gender = 'F'
  AND cd1.cd_education_status = 'Unknown'
  AND c_current_cdemo_sk = cd2.cd_demo_sk
  AND c_current_addr_sk = ca_address_sk
  AND c_birth_month IN (1, 6, 8, 9, 12, 2)
  AND d_year = 1998
  AND ca_state IN ('MS', 'IN', 'ND', 'OK', 'NM', 'VA', 'MS', 'TN')
GROUP BY ROLLUP (i_item_id, ca_country, ca_state, ca_county)
ORDER BY ca_country, ca_state, ca_county, i_item_id
LIMIT 100
