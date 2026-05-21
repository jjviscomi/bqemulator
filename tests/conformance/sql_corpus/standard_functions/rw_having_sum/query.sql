SELECT user_id, SUM(amount) AS total FROM `${DATASET}.events`
GROUP BY user_id HAVING SUM(amount) > NUMERIC '50.00' ORDER BY user_id
