SELECT user_id, event_type, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts) AS rn
FROM `${DATASET}.events`
ORDER BY user_id, rn
