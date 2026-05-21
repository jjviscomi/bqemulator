SELECT column_name, is_partitioning_column
FROM `${DATASET}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'daily_metrics'
ORDER BY ordinal_position
