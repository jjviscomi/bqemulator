SELECT _TABLE_SUFFIX AS suffix, COUNT(*) AS n FROM `${DATASET}.events_*`
GROUP BY suffix ORDER BY suffix
