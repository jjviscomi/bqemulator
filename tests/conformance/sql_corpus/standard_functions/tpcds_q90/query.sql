SELECT
  CAST(amc AS NUMERIC) / CAST(pmc AS NUMERIC) AS am_pm_ratio
FROM (
  SELECT COUNT(*) AS amc
  FROM `${DATASET}.web_sales` AS web_sales,
       `${DATASET}.household_demographics` AS household_demographics,
       `${DATASET}.time_dim` AS time_dim,
       `${DATASET}.web_page` AS web_page
  WHERE ws_sold_time_sk = time_dim.t_time_sk
    AND ws_ship_hdemo_sk = household_demographics.hd_demo_sk
    AND ws_web_page_sk = web_page.wp_web_page_sk
    AND time_dim.t_hour BETWEEN 8 AND 8 + 1
    AND household_demographics.hd_dep_count = 6
    AND web_page.wp_char_count BETWEEN 5000 AND 5200
) am_data,
(
  SELECT COUNT(*) AS pmc
  FROM `${DATASET}.web_sales` AS web_sales,
       `${DATASET}.household_demographics` AS household_demographics,
       `${DATASET}.time_dim` AS time_dim,
       `${DATASET}.web_page` AS web_page
  WHERE ws_sold_time_sk = time_dim.t_time_sk
    AND ws_ship_hdemo_sk = household_demographics.hd_demo_sk
    AND ws_web_page_sk = web_page.wp_web_page_sk
    AND time_dim.t_hour BETWEEN 19 AND 19 + 1
    AND household_demographics.hd_dep_count = 6
    AND web_page.wp_char_count BETWEEN 5000 AND 5200
) pt
ORDER BY am_pm_ratio
LIMIT 100
