CREATE TABLE `${DATASET}.clone_of_empty`
  CLONE `${DATASET}.empty_clone_source`;
SELECT COUNT(*) AS n FROM `${DATASET}.clone_of_empty`
