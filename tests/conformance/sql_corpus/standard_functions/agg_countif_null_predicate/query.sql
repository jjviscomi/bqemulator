SELECT
  COUNTIF(v IS NULL) AS n_null,
  COUNTIF(v IS NOT NULL) AS n_non_null,
  COUNTIF(v > 0) AS n_positive
FROM `${DATASET}.mixed`
