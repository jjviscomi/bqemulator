CREATE OR REPLACE TABLE `${DATASET}.snap_source` (id INT64, label STRING);
INSERT INTO `${DATASET}.snap_source` (id, label) VALUES (1, "alpha"), (2, NULL), (3, NULL), (4, "delta");
