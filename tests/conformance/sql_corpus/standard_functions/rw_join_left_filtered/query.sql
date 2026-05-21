SELECT a.user_id, b.event_type
FROM (SELECT DISTINCT user_id FROM `${DATASET}.events`) AS a
LEFT JOIN `${DATASET}.events` AS b ON a.user_id = b.user_id AND b.event_type = 'purchase'
ORDER BY a.user_id, b.event_type NULLS FIRST
