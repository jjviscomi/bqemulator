CREATE OR REPLACE TABLE `${DATASET}.events` AS
  SELECT "click" AS action, 1 AS user_id UNION ALL
  SELECT "view", 2 UNION ALL
  SELECT "click", 1 UNION ALL
  SELECT "buy", 3 UNION ALL
  SELECT "click", 4 UNION ALL
  SELECT "view", 4;
