SELECT a._TABLE_SUFFIX, b._TABLE_SUFFIX, a.id, b.id
FROM `${DATASET}.events_*` AS a
JOIN `${DATASET}.events_*` AS b
  ON a.event = b.event
  AND a._TABLE_SUFFIX < b._TABLE_SUFFIX
ORDER BY a._TABLE_SUFFIX, b._TABLE_SUFFIX, a.id
LIMIT 5
