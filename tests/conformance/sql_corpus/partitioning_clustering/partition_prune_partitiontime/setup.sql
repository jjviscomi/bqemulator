CREATE OR REPLACE TABLE `${DATASET}.ingest_ptime` (id INT64, payload STRING)
PARTITION BY _PARTITIONDATE;
INSERT INTO `${DATASET}.ingest_ptime` (id, payload) VALUES
  (10, 'p'),
  (20, 'q');
