WITH a AS (SELECT user_id FROM `${DATASET}.events` GROUP BY user_id),
     b AS (SELECT user_id, COUNT(*) AS c FROM `${DATASET}.events` GROUP BY user_id),
     c AS (SELECT user_id, SUM(amount) AS s FROM `${DATASET}.events` GROUP BY user_id)
SELECT a.user_id, b.c AS event_count, c.s AS total_spend
FROM a JOIN b USING (user_id) JOIN c USING (user_id)
ORDER BY a.user_id
