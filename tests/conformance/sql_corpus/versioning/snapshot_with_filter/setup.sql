CREATE OR REPLACE TABLE `${DATASET}.events` AS
  SELECT 1 AS id, "click" AS action, 100 AS amount UNION ALL
  SELECT 2, "view", 50 UNION ALL
  SELECT 3, "click", 200 UNION ALL
  SELECT 4, "buy", 1000 UNION ALL
  SELECT 5, "click", 75;
