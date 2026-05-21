SELECT user_id, ts,
  AVG(amount) OVER (PARTITION BY user_id ORDER BY ts
    ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS avg3
FROM `${DATASET}.events`
ORDER BY user_id, ts
