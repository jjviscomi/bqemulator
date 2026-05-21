WITH p AS (
  SELECT user_id, MIN(ts) AS first_purchase
  FROM `${DATASET}.events`
  WHERE event_type = 'purchase'
  GROUP BY user_id
)
SELECT user_id, first_purchase
FROM p
ORDER BY user_id
