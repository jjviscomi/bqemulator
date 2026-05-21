SELECT user_id, x
FROM (SELECT user_id, [1, 2, 3] AS arr FROM `${DATASET}.events` GROUP BY user_id) AS t,
     UNNEST(arr) AS x
ORDER BY user_id, x
