WITH cs_ui AS (
  SELECT cs_item_sk, SUM(cs_order_number) AS sale, SUM(cr_order_number) AS refund
  FROM `${DATASET}.catalog_sales` AS catalog_sales,
       `${DATASET}.catalog_returns` AS catalog_returns
  WHERE cs_item_sk = cr_item_sk
    AND cs_order_number = cr_order_number
  GROUP BY cs_item_sk
  HAVING SUM(cs_order_number) > 2 * SUM(cr_order_number)
),
cross_sales AS (
  SELECT
    i_product_name AS product_name,
    i_item_sk AS item_sk,
    s_store_name AS store_name,
    s_zip AS store_zip,
    ad1.ca_city AS b_city,
    ad2.ca_city AS c_city,
    d1.d_year AS syear,
    COUNT(*) AS cnt,
    SUM(ss_wholesale_cost) AS s1,
    SUM(ss_list_price) AS s2,
    SUM(ss_coupon_amt) AS s3
  FROM `${DATASET}.store_sales` AS store_sales,
       `${DATASET}.store_returns` AS store_returns,
       cs_ui,
       `${DATASET}.date_dim` AS d1,
       `${DATASET}.date_dim` AS d2,
       `${DATASET}.store` AS store,
       `${DATASET}.customer` AS customer,
       `${DATASET}.customer_demographics` AS cd1,
       `${DATASET}.customer_demographics` AS cd2,
       `${DATASET}.promotion` AS promotion,
       `${DATASET}.household_demographics` AS hd1,
       `${DATASET}.household_demographics` AS hd2,
       `${DATASET}.customer_address` AS ad1,
       `${DATASET}.customer_address` AS ad2,
       `${DATASET}.income_band` AS ib1,
       `${DATASET}.income_band` AS ib2,
       `${DATASET}.item` AS item
  WHERE ss_store_sk = s_store_sk
    AND ss_sold_date_sk = d1.d_date_sk
    AND ss_customer_sk = c_customer_sk
    AND ss_cdemo_sk = cd1.cd_demo_sk
    AND ss_hdemo_sk = hd1.hd_demo_sk
    AND ss_addr_sk = ad1.ca_address_sk
    AND ss_item_sk = i_item_sk
    AND ss_item_sk = sr_item_sk
    AND ss_ticket_number = sr_ticket_number
    AND ss_item_sk = cs_ui.cs_item_sk
    AND c_current_cdemo_sk = cd2.cd_demo_sk
    AND c_current_hdemo_sk = hd2.hd_demo_sk
    AND c_current_addr_sk = ad2.ca_address_sk
    AND c_first_sales_date_sk = d2.d_date_sk
    AND ss_promo_sk = p_promo_sk
    AND hd1.hd_income_band_sk = ib1.ib_income_band_sk
    AND hd2.hd_income_band_sk = ib2.ib_income_band_sk
    AND i_color IN ('purple', 'burlywood', 'indian', 'spring', 'floral', 'medium')
    AND i_current_price BETWEEN 64 AND 64 + 10
    AND i_current_price BETWEEN 64 + 1 AND 64 + 15
  GROUP BY i_product_name, i_item_sk, s_store_name, s_zip, ad1.ca_city,
           ad2.ca_city, d1.d_year
)
SELECT
  cs1.product_name, cs1.store_name, cs1.store_zip,
  cs1.b_city, cs1.c_city, cs1.syear, cs1.cnt
FROM cross_sales cs1, cross_sales cs2
WHERE cs1.item_sk = cs2.item_sk
  AND cs1.syear = 1999
  AND cs2.syear = 1999 + 1
  AND cs2.cnt <= cs1.cnt
  AND cs1.store_name = cs2.store_name
  AND cs1.store_zip = cs2.store_zip
ORDER BY cs1.product_name, cs1.store_name, cs2.cnt
LIMIT 100
