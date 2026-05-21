SELECT SUM(ss_quantity) AS total_quantity
FROM (
  SELECT
    1 AS ss_quantity, ss_sales_price, ss_net_profit,
    ss_store_sk, ss_cdemo_sk, ss_addr_sk, ss_sold_date_sk
  FROM `${DATASET}.store_sales`
) ss_pretend,
`${DATASET}.store` AS store,
`${DATASET}.customer_demographics` AS customer_demographics,
`${DATASET}.customer_address` AS customer_address,
`${DATASET}.date_dim` AS date_dim
WHERE ss_pretend.ss_store_sk = store.s_store_sk
  AND ss_pretend.ss_sold_date_sk = date_dim.d_date_sk
  AND date_dim.d_year = 2000
  AND (
    (customer_demographics.cd_demo_sk = ss_pretend.ss_cdemo_sk
     AND cd_marital_status = 'M'
     AND cd_education_status = '4 yr Degree'
     AND ss_sales_price BETWEEN 100.00 AND 150.00)
    OR
    (customer_demographics.cd_demo_sk = ss_pretend.ss_cdemo_sk
     AND cd_marital_status = 'D'
     AND cd_education_status = '2 yr Degree'
     AND ss_sales_price BETWEEN 50.00 AND 100.00)
    OR
    (customer_demographics.cd_demo_sk = ss_pretend.ss_cdemo_sk
     AND cd_marital_status = 'S'
     AND cd_education_status = 'College'
     AND ss_sales_price BETWEEN 150.00 AND 200.00)
  )
  AND (
    (ss_pretend.ss_addr_sk = customer_address.ca_address_sk
     AND ca_country = 'United States'
     AND ca_state IN ('OH', 'NJ', 'IL', 'TN')
     AND ss_net_profit BETWEEN 0 AND 2000)
    OR
    (ss_pretend.ss_addr_sk = customer_address.ca_address_sk
     AND ca_country = 'United States'
     AND ca_state IN ('IN', 'WI', 'MO')
     AND ss_net_profit BETWEEN 150 AND 3000)
    OR
    (ss_pretend.ss_addr_sk = customer_address.ca_address_sk
     AND ca_country = 'United States'
     AND ca_state IN ('WA', 'NC', 'SD', 'LA')
     AND ss_net_profit BETWEEN 50 AND 25000)
  )
LIMIT 100
