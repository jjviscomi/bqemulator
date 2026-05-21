WITH s AS (
  SELECT user_id, ts,
    IF(TIMESTAMP_DIFF(ts, LAG(ts) OVER (PARTITION BY user_id ORDER BY ts), SECOND) > 600
       OR LAG(ts) OVER (PARTITION BY user_id ORDER BY ts) IS NULL, 1, 0) AS new_session
  FROM `${DATASET}.events`
)
SELECT user_id, SUM(new_session) AS sessions FROM s GROUP BY user_id ORDER BY user_id
