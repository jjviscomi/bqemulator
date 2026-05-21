SELECT user_id FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`) AS u
WHERE user_id NOT IN (
  SELECT user_id FROM `${DATASET}.events` WHERE event_type = 'purchase'
)
ORDER BY user_id
