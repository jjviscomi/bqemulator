SELECT event, COUNT(*) AS n FROM `${DATASET}.events_*` GROUP BY event ORDER BY event
