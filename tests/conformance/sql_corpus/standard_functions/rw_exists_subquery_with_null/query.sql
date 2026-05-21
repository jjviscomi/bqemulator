SELECT
  EXISTS (SELECT 1 FROM `${DATASET}.with_nulls` WHERE v IS NULL) AS has_null_rows,
  EXISTS (SELECT 1 FROM `${DATASET}.with_nulls` WHERE v IS NOT NULL) AS has_non_null_rows
