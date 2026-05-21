SELECT user_id, MIN(ts) AS first_ts
FROM `${DATASET}.events`
GROUP BY user_id ORDER BY user_id
