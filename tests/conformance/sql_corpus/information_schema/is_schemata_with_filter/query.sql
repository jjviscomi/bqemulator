SELECT schema_name
FROM `region-us.INFORMATION_SCHEMA.SCHEMATA`
WHERE schema_name = '${DATASET_ID}'
