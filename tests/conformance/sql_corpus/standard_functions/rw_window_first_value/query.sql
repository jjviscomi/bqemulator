SELECT user_id, event_type,
  FIRST_VALUE(event_type) OVER (PARTITION BY user_id ORDER BY ts) AS first_event
FROM `${DATASET}.events`
ORDER BY user_id, ts
