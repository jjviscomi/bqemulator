CREATE OR REPLACE TABLE `${DATASET}.sales` (region STRING, amount NUMERIC);
INSERT INTO `${DATASET}.sales` (region, amount) VALUES
  ("east", NUMERIC "100.00"),
  ("east", NUMERIC "50.00"),
  ("west", NULL),
  ("west", NULL),
  ("south", NUMERIC "0.00");
