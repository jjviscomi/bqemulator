SELECT DISTINCT user_id FROM `${DATASET}.events`
WHERE event_type = 'purchase' ORDER BY user_id
