SELECT
  c_last_name,
  c_first_name,
  SUBSTR(s_city, 1, 30) AS city,
  ss_ticket_number,
  amt,
  profit
FROM
  (SELECT
     ss_ticket_number,
     ss_customer_sk,
     store.s_city,
     SUM(ss_coupon_amt) AS amt,
     SUM(ss_net_profit) AS profit
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.date_dim` AS date_dim,
        `${DATASET}.store` AS store,
        `${DATASET}.household_demographics` AS household_demographics
   WHERE store_sales.ss_sold_date_sk = date_dim.d_date_sk
     AND store_sales.ss_store_sk = store.s_store_sk
     AND store_sales.ss_hdemo_sk = household_demographics.hd_demo_sk
     AND (household_demographics.hd_dep_count = 6
          OR household_demographics.hd_vehicle_count > 2)
     AND date_dim.d_dow = 1
     AND date_dim.d_year IN (1999, 1999 + 1, 1999 + 2)
     AND store.s_number_employees BETWEEN 200 AND 295
   GROUP BY ss_ticket_number, ss_customer_sk, ss_addr_sk, store.s_city) ms,
  `${DATASET}.customer` AS customer
WHERE ss_customer_sk = c_customer_sk
ORDER BY c_last_name, c_first_name, SUBSTR(s_city, 1, 30), profit
LIMIT 100
