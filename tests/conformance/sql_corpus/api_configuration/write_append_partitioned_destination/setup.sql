CREATE OR REPLACE TABLE `${DATASET}`.target (event_date DATE, value INT64)
PARTITION BY event_date;
INSERT INTO `${DATASET}`.target (event_date, value) VALUES (DATE '2024-01-01', 1);
