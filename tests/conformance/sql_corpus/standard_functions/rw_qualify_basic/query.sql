SELECT user_id, event_type, ts FROM `${DATASET}.events`
QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts) = 1
ORDER BY user_id
