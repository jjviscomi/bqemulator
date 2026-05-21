SELECT total_rows, storage_tier
FROM `${DATASET}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = 'p_ingest'
