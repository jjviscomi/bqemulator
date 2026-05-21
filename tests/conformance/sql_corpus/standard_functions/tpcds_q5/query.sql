WITH ssr AS (
  SELECT
    s_store_id,
    SUM(sales_price) AS sales,
    SUM(profit) AS profit,
    SUM(return_amt) AS returns_amt,
    SUM(net_loss) AS profit_loss
  FROM (
    SELECT
      ss_store_sk AS store_sk, ss_sold_date_sk AS date_sk,
      ss_ext_sales_price AS sales_price, ss_net_profit AS profit,
      CAST(0 AS NUMERIC) AS return_amt, CAST(0 AS NUMERIC) AS net_loss
    FROM `${DATASET}.store_sales` AS store_sales
    UNION ALL
    SELECT
      sr_store_sk AS store_sk, sr_returned_date_sk AS date_sk,
      CAST(0 AS NUMERIC) AS sales_price, CAST(0 AS NUMERIC) AS profit,
      sr_return_amt AS return_amt, sr_net_loss AS net_loss
    FROM `${DATASET}.store_returns` AS store_returns
  ) salesreturns, `${DATASET}.date_dim` AS date_dim, `${DATASET}.store` AS store
  WHERE date_sk = d_date_sk
    AND d_date BETWEEN DATE '2001-11-01' AND DATE_ADD(DATE '2001-11-01', INTERVAL 14 DAY)
    AND store_sk = s_store_sk
  GROUP BY s_store_id
),
csr AS (
  SELECT
    cp_catalog_page_id,
    SUM(sales_price) AS sales,
    SUM(profit) AS profit,
    SUM(return_amt) AS returns_amt,
    SUM(net_loss) AS profit_loss
  FROM (
    SELECT
      cs_catalog_page_sk AS page_sk, cs_sold_date_sk AS date_sk,
      cs_ext_sales_price AS sales_price, cs_net_profit AS profit,
      CAST(0 AS NUMERIC) AS return_amt, CAST(0 AS NUMERIC) AS net_loss
    FROM `${DATASET}.catalog_sales` AS catalog_sales
    UNION ALL
    SELECT
      cr_catalog_page_sk AS page_sk, cr_returned_date_sk AS date_sk,
      CAST(0 AS NUMERIC) AS sales_price, CAST(0 AS NUMERIC) AS profit,
      cr_return_amount AS return_amt, cr_net_loss AS net_loss
    FROM `${DATASET}.catalog_returns` AS catalog_returns
  ) salesreturns, `${DATASET}.date_dim` AS date_dim, `${DATASET}.catalog_page` AS catalog_page
  WHERE date_sk = d_date_sk
    AND d_date BETWEEN DATE '2001-11-01' AND DATE_ADD(DATE '2001-11-01', INTERVAL 14 DAY)
    AND page_sk = cp_catalog_page_sk
  GROUP BY cp_catalog_page_id
),
wsr AS (
  SELECT
    web_site_id,
    SUM(sales_price) AS sales,
    SUM(profit) AS profit,
    SUM(return_amt) AS returns_amt,
    SUM(net_loss) AS profit_loss
  FROM (
    SELECT
      ws_web_site_sk AS wsr_web_site_sk, ws_sold_date_sk AS date_sk,
      ws_ext_sales_price AS sales_price, ws_net_profit AS profit,
      CAST(0 AS NUMERIC) AS return_amt, CAST(0 AS NUMERIC) AS net_loss
    FROM `${DATASET}.web_sales` AS web_sales
    UNION ALL
    SELECT
      ws_web_site_sk AS wsr_web_site_sk, wr_returned_date_sk AS date_sk,
      CAST(0 AS NUMERIC) AS sales_price, CAST(0 AS NUMERIC) AS profit,
      wr_return_amt AS return_amt, wr_net_loss AS net_loss
    FROM `${DATASET}.web_returns` AS web_returns
    LEFT OUTER JOIN `${DATASET}.web_page` AS web_page
      ON wr_web_page_sk = wp_web_page_sk
    LEFT OUTER JOIN `${DATASET}.web_sales` AS web_sales
      ON wp_web_site_sk = ws_web_site_sk
  ) salesreturns, `${DATASET}.date_dim` AS date_dim, `${DATASET}.web_site` AS web_site
  WHERE date_sk = d_date_sk
    AND d_date BETWEEN DATE '2001-11-01' AND DATE_ADD(DATE '2001-11-01', INTERVAL 14 DAY)
    AND wsr_web_site_sk = web_site_sk
  GROUP BY web_site_id
)
SELECT
  channel,
  id,
  SUM(sales) AS sales,
  SUM(returns_amt) AS returns_amt,
  SUM(profit) AS profit
FROM (
  SELECT 'store channel' AS channel, CONCAT('store', s_store_id) AS id,
         sales, returns_amt, (profit - profit_loss) AS profit
  FROM ssr
  UNION ALL
  SELECT 'catalog channel' AS channel, CONCAT('catalog_page', cp_catalog_page_id) AS id,
         sales, returns_amt, (profit - profit_loss) AS profit
  FROM csr
  UNION ALL
  SELECT 'web channel' AS channel, CONCAT('web_site', web_site_id) AS id,
         sales, returns_amt, (profit - profit_loss) AS profit
  FROM wsr
) x
GROUP BY channel, id
ORDER BY channel, id
LIMIT 100
