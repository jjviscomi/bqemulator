SELECT user_id, event_type,
  TIMESTAMP_DIFF(ts, LAG(ts) OVER (PARTITION BY user_id ORDER BY ts), SECOND) AS sec_since_prev
FROM `${DATASET}.events`
ORDER BY user_id, ts
