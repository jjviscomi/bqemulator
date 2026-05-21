SELECT event_type, COUNT(*) AS n
FROM `${DATASET}.events`
GROUP BY event_type ORDER BY event_type
