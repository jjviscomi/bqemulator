SELECT user_id, ts,
  SUM(amount) OVER (PARTITION BY user_id ORDER BY ts
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_revenue
FROM `${DATASET}.events`
ORDER BY user_id, ts
