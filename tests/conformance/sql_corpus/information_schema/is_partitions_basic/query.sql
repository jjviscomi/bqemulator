SELECT partition_id, total_rows, storage_tier
FROM `${DATASET}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = 'p_basic'
ORDER BY partition_id
