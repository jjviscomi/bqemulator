SELECT user_id, region, active, session_range
FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.sessions`,
  'active',
  ['user_id', 'region']
)
ORDER BY user_id, region, active
