SELECT * FROM RANGE_SESSIONIZE(
  TABLE `${DATASET}.events`,
  'r',
  ['session_id'],
  'BOGUS_MODE'
)
