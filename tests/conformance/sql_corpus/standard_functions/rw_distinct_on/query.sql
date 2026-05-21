SELECT user_id, event_type, ts FROM (
  SELECT user_id, event_type, ts,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts) AS rn
  FROM `${DATASET}.events`
)
WHERE rn = 1 ORDER BY user_id
