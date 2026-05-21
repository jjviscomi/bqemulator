CREATE OR REPLACE TABLE `${DATASET}.users` AS
  SELECT 1 AS user_id, "alice" AS name UNION ALL
  SELECT 2, "bob" UNION ALL
  SELECT 3, "carol";
CREATE OR REPLACE TABLE `${DATASET}.activity` AS
  SELECT 1 AS user_id, "click" AS action UNION ALL
  SELECT 2, "view" UNION ALL
  SELECT 1, "buy" UNION ALL
  SELECT 3, "view";
