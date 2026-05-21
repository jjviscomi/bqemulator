SELECT user_id, event_type,
  LAST_VALUE(event_type) OVER (
    PARTITION BY user_id ORDER BY ts
    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
  ) AS last_event
FROM `${DATASET}.events`
ORDER BY user_id, ts
