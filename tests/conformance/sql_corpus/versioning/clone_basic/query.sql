CREATE OR REPLACE TABLE `${DATASET}.cloned_table`
CLONE `${DATASET}.source_table`;

SELECT id, label FROM `${DATASET}.cloned_table` ORDER BY id
