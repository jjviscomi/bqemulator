SELECT
  ARRAY_AGG(IF(event_type = 'purchase', user_id, NULL) IGNORE NULLS ORDER BY user_id) AS buyers
FROM `${DATASET}.events`
