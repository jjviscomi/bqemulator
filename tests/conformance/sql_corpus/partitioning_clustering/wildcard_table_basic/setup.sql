CREATE OR REPLACE TABLE `${DATASET}.events_20240101` AS
  SELECT 1 AS id, "click" AS event UNION ALL
  SELECT 2 AS id, "view" AS event;

CREATE OR REPLACE TABLE `${DATASET}.events_20240102` AS
  SELECT 3 AS id, "click" AS event UNION ALL
  SELECT 4 AS id, "purchase" AS event;

CREATE OR REPLACE TABLE `${DATASET}.events_20240103` AS
  SELECT 5 AS id, "click" AS event UNION ALL
  SELECT 6 AS id, "view" AS event;
