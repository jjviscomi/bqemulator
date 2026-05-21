CREATE OR REPLACE MATERIALIZED VIEW `${DATASET}.mv_empty` AS
  SELECT id, SUM(amount) AS total
  FROM `${DATASET}.empty_mv_base`
  GROUP BY id;
SELECT COUNT(*) AS n FROM `${DATASET}.mv_empty`
