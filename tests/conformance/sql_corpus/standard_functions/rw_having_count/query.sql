SELECT user_id, COUNT(*) AS c FROM `${DATASET}.events`
GROUP BY user_id HAVING COUNT(*) >= 3 ORDER BY user_id
