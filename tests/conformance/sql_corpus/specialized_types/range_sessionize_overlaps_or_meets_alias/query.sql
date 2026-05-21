SELECT user_id, duration, session_range
FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.events`,
  'duration',
  ['user_id'],
  'OVERLAPS_OR_MEETS'
)
ORDER BY user_id, duration
