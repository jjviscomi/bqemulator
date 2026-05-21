SELECT SUBSTR(_TABLE_SUFFIX, 1, 6) AS month_key, COUNT(*) AS n
FROM `${DATASET}.events_*`
GROUP BY month_key ORDER BY month_key
