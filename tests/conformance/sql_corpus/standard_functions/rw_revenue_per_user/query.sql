SELECT user_id, SUM(amount) AS revenue FROM `${DATASET}.events`
WHERE event_type = 'purchase' GROUP BY user_id ORDER BY user_id
