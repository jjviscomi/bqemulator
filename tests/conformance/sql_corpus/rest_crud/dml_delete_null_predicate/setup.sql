CREATE OR REPLACE TABLE `${DATASET}.items` (id INT64, label STRING);
INSERT INTO `${DATASET}.items` (id, label) VALUES
  (1, "alpha"), (2, NULL), (3, "gamma"), (4, NULL), (5, "epsilon");
