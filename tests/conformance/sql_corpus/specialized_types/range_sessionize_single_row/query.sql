SELECT user_id, duration, session_range
FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.events`,
  'duration',
  ['user_id']
)
ORDER BY user_id, duration
