CREATE MATERIALIZED VIEW `${DATASET}.click_counts`
AS SELECT user_id, COUNT(*) AS clicks
FROM `${DATASET}.events`
WHERE action = 'click'
GROUP BY user_id;

SELECT user_id, clicks FROM `${DATASET}.click_counts` ORDER BY user_id
