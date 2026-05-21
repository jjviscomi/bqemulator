CREATE OR REPLACE TABLE `${DATASET}.events` (event_id INT64, kind STRING);
INSERT INTO `${DATASET}.events` (event_id, kind) VALUES
  (1, "view"), (2, "view"), (3, "click"), (4, "click");
