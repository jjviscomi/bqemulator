SELECT user_id, event_type, n
FROM (
  SELECT 1 AS user_id, 1 AS view, 2 AS click, 3 AS purchase
  UNION ALL
  SELECT 2 AS user_id, 4 AS view, 5 AS click, 6 AS purchase
)
UNPIVOT (n FOR event_type IN (view, click, purchase))
ORDER BY user_id, event_type
