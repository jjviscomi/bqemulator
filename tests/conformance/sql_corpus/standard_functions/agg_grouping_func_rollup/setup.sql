CREATE OR REPLACE TABLE `${DATASET}.events` (region STRING, n INT64);
INSERT INTO `${DATASET}.events` (region, n) VALUES
  ("us", 1), ("us", 2), ("eu", 3), ("eu", 4), ("apac", 5);
