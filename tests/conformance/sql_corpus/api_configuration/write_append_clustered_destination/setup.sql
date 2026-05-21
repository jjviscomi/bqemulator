CREATE OR REPLACE TABLE `${DATASET}`.target (region STRING, value INT64)
CLUSTER BY region;
INSERT INTO `${DATASET}`.target (region, value) VALUES ('us', 1);
