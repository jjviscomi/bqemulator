SELECT COUNT(*) AS dataset_count
FROM `region-us.INFORMATION_SCHEMA.SCHEMATA`
WHERE schema_name = 'definitely_does_not_exist_${DATASET_ID}'
