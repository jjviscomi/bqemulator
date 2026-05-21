WITH ev AS (
  SELECT user_id, event_type, ts,
    LAG(event_type) OVER (PARTITION BY user_id ORDER BY ts) AS prev_type
  FROM `${DATASET}.events`
)
SELECT COUNT(*) AS n FROM ev
WHERE event_type = 'purchase' AND prev_type IN ('view', 'click')
