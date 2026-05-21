WITH purchases AS (
  SELECT user_id, SUM(amount) AS spend
  FROM `${DATASET}.events`
  WHERE event_type = 'purchase'
  GROUP BY user_id
),
ranked AS (
  SELECT user_id, spend, RANK() OVER (ORDER BY spend DESC) AS r
  FROM purchases
)
SELECT user_id, spend, r FROM ranked ORDER BY r, user_id
