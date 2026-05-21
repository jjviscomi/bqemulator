SELECT
  asceding.rnk,
  i1.i_product_name AS best_performing,
  i2.i_product_name AS worst_performing
FROM (
  SELECT *
  FROM (
    SELECT
      item_sk,
      rank() OVER (ORDER BY rank_col ASC) AS rnk
    FROM (
      SELECT
        ss_item_sk AS item_sk,
        AVG(ss_net_profit) AS rank_col
      FROM `${DATASET}.store_sales` AS ss1
      WHERE ss_store_sk = 4
      GROUP BY ss_item_sk
      HAVING AVG(ss_net_profit) > 0.9 * (
        SELECT AVG(ss_net_profit) AS rank_col
        FROM `${DATASET}.store_sales` AS sub
        WHERE ss_store_sk = 4
          AND ss_addr_sk IS NULL
        GROUP BY ss_store_sk
      )
    ) v1
  ) v11
  WHERE rnk < 11
) asceding,
(
  SELECT *
  FROM (
    SELECT
      item_sk,
      rank() OVER (ORDER BY rank_col DESC) AS rnk
    FROM (
      SELECT
        ss_item_sk AS item_sk,
        AVG(ss_net_profit) AS rank_col
      FROM `${DATASET}.store_sales` AS ss1
      WHERE ss_store_sk = 4
      GROUP BY ss_item_sk
      HAVING AVG(ss_net_profit) > 0.9 * (
        SELECT AVG(ss_net_profit) AS rank_col
        FROM `${DATASET}.store_sales` AS sub
        WHERE ss_store_sk = 4
          AND ss_addr_sk IS NULL
        GROUP BY ss_store_sk
      )
    ) v2
  ) v21
  WHERE rnk < 11
) descending,
`${DATASET}.item` AS i1,
`${DATASET}.item` AS i2
WHERE asceding.rnk = descending.rnk
  AND i1.i_item_sk = asceding.item_sk
  AND i2.i_item_sk = descending.item_sk
ORDER BY asceding.rnk
LIMIT 100
