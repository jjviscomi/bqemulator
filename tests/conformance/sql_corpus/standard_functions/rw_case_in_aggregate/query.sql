SELECT
  SUM(CASE WHEN event_type = 'purchase' THEN amount ELSE NUMERIC '0' END) AS purchase_revenue,
  SUM(CASE WHEN event_type = 'view' THEN 1 ELSE 0 END) AS view_count
FROM `${DATASET}.events`
