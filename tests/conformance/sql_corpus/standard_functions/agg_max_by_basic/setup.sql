CREATE OR REPLACE TABLE `${DATASET}.scores` (label STRING, amount NUMERIC);
INSERT INTO `${DATASET}.scores` (label, amount) VALUES
  ("alpha", NUMERIC "10.00"),
  ("beta", NUMERIC "50.00"),
  ("gamma", NUMERIC "30.00"),
  ("delta", NUMERIC "20.00");
