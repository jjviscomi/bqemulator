CREATE SNAPSHOT TABLE `${DATASET}.snap_table`
CLONE `${DATASET}.source_table`;

SELECT id, label FROM `${DATASET}.snap_table` ORDER BY id
