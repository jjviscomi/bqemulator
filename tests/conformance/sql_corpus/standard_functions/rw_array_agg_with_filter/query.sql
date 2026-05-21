SELECT user_id,
  ARRAY_AGG(event_type IGNORE NULLS ORDER BY ts) AS events
FROM `${DATASET}.events`
GROUP BY user_id ORDER BY user_id
