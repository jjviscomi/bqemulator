SELECT
  (1 IN (SELECT 1 LIMIT 0)) AS r_value_empty,
  (CAST(NULL AS INT64) IN (SELECT 1 LIMIT 0)) AS r_null_empty
