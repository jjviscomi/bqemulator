CREATE OR REPLACE TABLE `${DATASET}.records` (label STRING, score INT64);
INSERT INTO `${DATASET}.records` (label, score) VALUES
  ("alpha", 10),
  ("orphan_a", NULL),
  ("beta", 25),
  ("orphan_b", NULL),
  ("gamma", 17);
