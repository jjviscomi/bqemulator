CREATE OR REPLACE TABLE `${DATASET}.events` (region STRING, channel STRING, n INT64);
INSERT INTO `${DATASET}.events` (region, channel, n) VALUES
  ("us", "web", 10),
  ("us", "mobile", 20),
  ("eu", "web", 30),
  ("eu", "mobile", 40);
