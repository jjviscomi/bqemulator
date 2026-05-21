CREATE SNAPSHOT TABLE `${DATASET}.snap_with_nulls`
  CLONE `${DATASET}.snap_source`;
SELECT id, label FROM `${DATASET}.snap_with_nulls` ORDER BY id
