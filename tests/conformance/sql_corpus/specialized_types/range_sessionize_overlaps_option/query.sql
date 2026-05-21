SELECT emp_id, duration, session_range
FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.windows`,
  'duration',
  ['emp_id'],
  'OVERLAPS'
)
ORDER BY emp_id, duration
