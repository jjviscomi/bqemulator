WITH customer_total_return AS (
  SELECT
    sr_customer_sk AS ctr_customer_sk,
    ca_state       AS ctr_state,
    SUM(sr_return_amt_inc_tax) AS ctr_total_return
  FROM `${DATASET}.store_returns` AS store_returns,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.customer_address` AS customer_address
  WHERE sr_returned_date_sk = d_date_sk
    AND d_year = 2000
    AND sr_addr_sk = ca_address_sk
  GROUP BY sr_customer_sk, ca_state
)
SELECT
  c_customer_id,
  c_salutation,
  c_first_name,
  c_last_name,
  ca_street_number,
  ca_street_name,
  ca_city,
  ca_zip,
  ca_country,
  ca_gmt_offset,
  ca_location_type,
  ctr_total_return
FROM customer_total_return ctr1,
     `${DATASET}.customer_address` AS customer_address,
     `${DATASET}.customer` AS customer
WHERE ctr1.ctr_total_return > (
        SELECT AVG(ctr_total_return) * 1.2
        FROM customer_total_return ctr2
        WHERE ctr1.ctr_state = ctr2.ctr_state)
  AND ca_address_sk = c_current_addr_sk
  AND ca_state = 'GA'
  AND ctr1.ctr_customer_sk = c_customer_sk
ORDER BY
  c_customer_id, c_salutation, c_first_name, c_last_name,
  ca_street_number, ca_street_name, ca_city, ca_zip,
  ca_country, ca_gmt_offset, ca_location_type, ctr_total_return
LIMIT 100
