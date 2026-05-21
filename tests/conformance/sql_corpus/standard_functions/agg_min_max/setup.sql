CREATE OR REPLACE TABLE `${DATASET}.nums` (n INT64, label STRING, amount NUMERIC);
INSERT INTO `${DATASET}.nums` (n, label, amount) VALUES
  (1, "a", NUMERIC "10.00"), (2, "b", NUMERIC "20.00"),
  (3, "a", NUMERIC "30.00"), (4, "c", NUMERIC "40.00"),
  (5, "b", NUMERIC "50.00");
