SELECT COUNT(*) AS partition_count
FROM `${DATASET}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = 'p_empty'
