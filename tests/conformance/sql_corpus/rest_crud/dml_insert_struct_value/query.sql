CREATE OR REPLACE TABLE `${DATASET}.struct_t` (id INT64, person STRUCT<name STRING, age INT64>);
INSERT INTO `${DATASET}.struct_t` VALUES (1, STRUCT('Alice', 30));
SELECT id, person FROM `${DATASET}.struct_t`
