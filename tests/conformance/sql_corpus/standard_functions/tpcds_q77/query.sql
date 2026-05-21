WITH ss AS (
  SELECT s_store_sk,
         SUM(ss_ext_sales_price) AS sales,
         SUM(ss_net_profit) AS profit
  FROM `${DATASET}.store_sales` AS store_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.store` AS store
  WHERE ss_sold_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
    AND ss_store_sk = s_store_sk
  GROUP BY s_store_sk
),
sr AS (
  SELECT s_store_sk,
         SUM(sr_return_amt) AS returns_amt,
         SUM(sr_net_loss) AS profit_loss
  FROM `${DATASET}.store_returns` AS store_returns,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.store` AS store
  WHERE sr_returned_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
    AND sr_store_sk = s_store_sk
  GROUP BY s_store_sk
),
cs AS (
  SELECT cs_catalog_page_sk,
         SUM(cs_ext_sales_price) AS sales,
         SUM(cs_net_profit) AS profit
  FROM `${DATASET}.catalog_sales` AS catalog_sales,
       `${DATASET}.date_dim` AS date_dim
  WHERE cs_sold_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
  GROUP BY cs_catalog_page_sk
),
cr AS (
  SELECT cr_catalog_page_sk,
         SUM(cr_return_amount) AS returns_amt,
         SUM(cr_net_loss) AS profit_loss
  FROM `${DATASET}.catalog_returns` AS catalog_returns,
       `${DATASET}.date_dim` AS date_dim
  WHERE cr_returned_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
  GROUP BY cr_catalog_page_sk
),
ws AS (
  SELECT web_site_sk,
         SUM(ws_ext_sales_price) AS sales,
         SUM(ws_net_profit) AS profit
  FROM `${DATASET}.web_sales` AS web_sales,
       `${DATASET}.date_dim` AS date_dim,
       `${DATASET}.web_site` AS web_site
  WHERE ws_sold_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
    AND ws_web_site_sk = web_site_sk
  GROUP BY web_site_sk
),
wr AS (
  SELECT
    CAST(NULL AS INT64) AS web_site_sk,
    SUM(wr_return_amt) AS returns_amt,
    SUM(wr_net_loss) AS profit_loss
  FROM `${DATASET}.web_returns` AS web_returns,
       `${DATASET}.date_dim` AS date_dim
  WHERE wr_returned_date_sk = d_date_sk
    AND d_date BETWEEN DATE '2000-08-23' AND DATE_ADD(DATE '2000-08-23', INTERVAL 30 DAY)
)
SELECT
  channel, id,
  SUM(sales) AS sales,
  SUM(returns_amt) AS returns_amt,
  SUM(profit) AS profit
FROM (
  SELECT 'store channel' AS channel,
         ss.s_store_sk AS id,
         sales,
         COALESCE(returns_amt, NUMERIC "0") AS returns_amt,
         (profit - COALESCE(profit_loss, NUMERIC "0")) AS profit
  FROM ss LEFT JOIN sr ON ss.s_store_sk = sr.s_store_sk
  UNION ALL
  SELECT 'catalog channel' AS channel,
         cs_catalog_page_sk AS id,
         sales,
         returns_amt,
         (profit - profit_loss) AS profit
  FROM cs, cr
  UNION ALL
  SELECT 'web channel' AS channel,
         ws.web_site_sk AS id,
         sales,
         COALESCE(returns_amt, NUMERIC "0") AS returns_amt,
         (profit - COALESCE(profit_loss, NUMERIC "0")) AS profit
  FROM ws LEFT JOIN wr ON 1 = 1
) x
GROUP BY ROLLUP (channel, id)
ORDER BY channel, id
LIMIT 100
