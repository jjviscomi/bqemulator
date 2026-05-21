SELECT _TABLE_SUFFIX AS suffix, id, event
FROM `${DATASET}.events_*`
ORDER BY suffix, id
