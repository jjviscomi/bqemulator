-- Two tables are created; one is dropped. In BigQuery a dropped table
-- is removed from the catalog immediately, so it must disappear from
-- INFORMATION_SCHEMA.TABLES — only keep_t should remain. Pins the
-- DROP TABLE catalog-sync behaviour (ADR 0023 Bucket F amendment).
CREATE OR REPLACE TABLE `${DATASET}.keep_t` (id INT64);
CREATE OR REPLACE TABLE `${DATASET}.drop_t` (id INT64);
DROP TABLE `${DATASET}.drop_t`;
