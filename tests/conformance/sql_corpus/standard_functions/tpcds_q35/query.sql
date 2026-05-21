SELECT
  ca_state,
  cd_gender,
  cd_marital_status,
  cd_dep_count,
  COUNT(*) AS cnt1,
  MIN(cd_dep_count) AS min1,
  MAX(cd_dep_count) AS max1,
  AVG(cd_dep_count) AS avg1,
  cd_dep_employed_count,
  COUNT(*) AS cnt2,
  MIN(cd_dep_employed_count) AS min2,
  MAX(cd_dep_employed_count) AS max2,
  AVG(cd_dep_employed_count) AS avg2,
  cd_dep_college_count,
  COUNT(*) AS cnt3,
  MIN(cd_dep_college_count) AS min3,
  MAX(cd_dep_college_count) AS max3,
  AVG(cd_dep_college_count) AS avg3
FROM `${DATASET}.customer` AS c, `${DATASET}.customer_address` AS ca,
     `${DATASET}.customer_demographics` AS customer_demographics
WHERE c.c_current_addr_sk = ca.ca_address_sk
  AND cd_demo_sk = c.c_current_cdemo_sk
  AND EXISTS (
    SELECT *
    FROM `${DATASET}.store_sales`, `${DATASET}.date_dim`
    WHERE c.c_customer_sk = ss_customer_sk
      AND ss_sold_date_sk = d_date_sk
      AND d_year = 2002
      AND d_qoy < 4
  )
  AND (
    EXISTS (
      SELECT *
      FROM `${DATASET}.web_sales`, `${DATASET}.date_dim`
      WHERE c.c_customer_sk = ws_bill_customer_sk
        AND ws_sold_date_sk = d_date_sk
        AND d_year = 2002
        AND d_qoy < 4
    )
    OR
    EXISTS (
      SELECT *
      FROM `${DATASET}.catalog_sales`, `${DATASET}.date_dim`
      WHERE c.c_customer_sk = cs_ship_customer_sk
        AND cs_sold_date_sk = d_date_sk
        AND d_year = 2002
        AND d_qoy < 4
    )
  )
GROUP BY ca_state, cd_gender, cd_marital_status, cd_dep_count,
         cd_dep_employed_count, cd_dep_college_count
ORDER BY ca_state, cd_gender, cd_marital_status, cd_dep_count,
         cd_dep_employed_count, cd_dep_college_count
LIMIT 100
