SELECT DISTINCT user_id FROM `${DATASET}.events` AS e
WHERE EXISTS (SELECT 1 FROM `${DATASET}.events` AS e2
              WHERE e2.user_id = e.user_id AND e2.event_type = 'purchase')
ORDER BY user_id
