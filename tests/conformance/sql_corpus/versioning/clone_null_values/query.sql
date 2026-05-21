CREATE TABLE `${DATASET}.clone_with_nulls`
  CLONE `${DATASET}.clone_source`;
SELECT id, label FROM `${DATASET}.clone_with_nulls` ORDER BY id
