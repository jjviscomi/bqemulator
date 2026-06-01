-- Drop a table, then recreate it under the same name with a different
-- schema. Queries must see the recreated table's columns, not the
-- dropped table's — the DROP must clear the old catalog entry so the
-- recreate registers cleanly (parallels ddl_drop_view_recreate).
CREATE OR REPLACE TABLE `${DATASET}.t_recreate` (id INT64);
INSERT INTO `${DATASET}.t_recreate` (id) VALUES (1), (2);
DROP TABLE `${DATASET}.t_recreate`;
CREATE OR REPLACE TABLE `${DATASET}.t_recreate` (val STRING);
INSERT INTO `${DATASET}.t_recreate` (val) VALUES ('a'), ('b');
