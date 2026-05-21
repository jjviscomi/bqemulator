SELECT user_id,
  (SELECT MAX(ts) FROM `${DATASET}.events` AS e2 WHERE e2.user_id = e1.user_id) AS last_ts
FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`) AS e1
ORDER BY user_id
