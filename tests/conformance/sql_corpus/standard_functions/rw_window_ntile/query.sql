SELECT user_id, NTILE(3) OVER (ORDER BY user_id) AS bucket
FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`)
ORDER BY user_id
