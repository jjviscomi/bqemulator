SELECT user_id, ROUND(PERCENT_RANK() OVER (ORDER BY user_id), 6) AS pr
FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`)
ORDER BY user_id
