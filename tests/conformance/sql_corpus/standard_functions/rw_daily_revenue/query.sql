SELECT DATE(ts) AS day, SUM(amount) AS revenue
FROM `${DATASET}.events` WHERE event_type = 'purchase'
GROUP BY day ORDER BY day
