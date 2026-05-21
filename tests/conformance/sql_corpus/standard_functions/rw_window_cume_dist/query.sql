SELECT user_id, ROUND(CUME_DIST() OVER (ORDER BY user_id), 6) AS cd
FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`)
ORDER BY user_id
