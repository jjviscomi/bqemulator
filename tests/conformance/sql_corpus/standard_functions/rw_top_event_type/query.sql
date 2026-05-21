SELECT event_type, c FROM (
  SELECT event_type, COUNT(*) AS c, RANK() OVER (ORDER BY COUNT(*) DESC) AS r
  FROM `${DATASET}.events` GROUP BY event_type
)
WHERE r = 1 ORDER BY event_type
