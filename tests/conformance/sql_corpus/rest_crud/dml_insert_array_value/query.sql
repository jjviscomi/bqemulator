CREATE OR REPLACE TABLE `${DATASET}.arr_t` (id INT64, tags ARRAY<STRING>);
INSERT INTO `${DATASET}.arr_t` VALUES (1, ['a','b','c']);
SELECT id, tags FROM `${DATASET}.arr_t`
