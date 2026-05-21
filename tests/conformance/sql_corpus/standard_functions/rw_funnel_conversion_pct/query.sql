SELECT
  ROUND(SAFE_DIVIDE(COUNTIF(event_type = 'purchase'), COUNTIF(event_type = 'view')) * 100, 2) AS conv_pct
FROM `${DATASET}.events`
