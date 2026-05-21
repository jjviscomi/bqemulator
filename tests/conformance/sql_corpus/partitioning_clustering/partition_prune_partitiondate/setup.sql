CREATE OR REPLACE TABLE `${DATASET}.ingest_events` (id INT64, payload STRING)
PARTITION BY _PARTITIONDATE;
INSERT INTO `${DATASET}.ingest_events` (id, payload) VALUES
  (1, 'a'),
  (2, 'b'),
  (3, 'c');
