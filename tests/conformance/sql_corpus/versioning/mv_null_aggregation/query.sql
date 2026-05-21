CREATE OR REPLACE MATERIALIZED VIEW `${DATASET}.mv_sum_with_nulls` AS
  SELECT group_id, SUM(amount) AS total, COUNT(amount) AS n_non_null
  FROM `${DATASET}.base_with_nulls`
  GROUP BY group_id;
SELECT group_id, total, n_non_null FROM `${DATASET}.mv_sum_with_nulls` ORDER BY group_id
