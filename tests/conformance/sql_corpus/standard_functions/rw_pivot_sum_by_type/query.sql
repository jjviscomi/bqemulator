SELECT * FROM (
  SELECT user_id, event_type, amount FROM `${DATASET}.events`
)
PIVOT (SUM(amount) FOR event_type IN ('view', 'click', 'purchase'))
ORDER BY user_id
