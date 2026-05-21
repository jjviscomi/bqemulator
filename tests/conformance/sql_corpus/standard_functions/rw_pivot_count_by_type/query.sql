SELECT * FROM (
  SELECT user_id, event_type FROM `${DATASET}.events`
)
PIVOT (COUNT(*) FOR event_type IN ('view', 'click', 'purchase'))
ORDER BY user_id
