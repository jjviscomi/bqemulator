CREATE SNAPSHOT TABLE `${DATASET}.snap_of_empty`
  CLONE `${DATASET}.empty_snap_source`;
SELECT COUNT(*) AS n FROM `${DATASET}.snap_of_empty`
