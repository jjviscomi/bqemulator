SELECT org, user_id, region, active, session_range
FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.events`,
  'active',
  ['org', 'user_id', 'region']
)
ORDER BY org, user_id, region, active
