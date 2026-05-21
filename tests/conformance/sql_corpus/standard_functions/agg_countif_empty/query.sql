SELECT
  COUNTIF(v > 0) AS n_positive,
  COUNTIF(v IS NULL) AS n_null,
  COUNTIF(TRUE) AS n_true
FROM `${DATASET}.empty_countif_t`
