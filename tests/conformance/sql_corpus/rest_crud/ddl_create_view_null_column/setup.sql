CREATE OR REPLACE TABLE `${DATASET}.source_data` (id INT64, name STRING, note STRING);
INSERT INTO `${DATASET}.source_data` (id, name, note) VALUES
  (1, "Alice", NULL), (2, "Bob", "ok"), (3, NULL, NULL);
