CREATE OR REPLACE TABLE `${DATASET}.read_data` (
  id INT64,
  value STRING,
  category STRING
);

INSERT INTO `${DATASET}.read_data` VALUES
  (1, 'alpha', 'a'),
  (2, 'beta', 'a'),
  (3, 'gamma', 'b'),
  (4, 'delta', 'b'),
  (5, 'epsilon', 'a'),
  (6, 'zeta', 'b'),
  (7, 'eta', 'a'),
  (8, 'theta', 'b'),
  (9, 'iota', 'a'),
  (10, 'kappa', 'b');
