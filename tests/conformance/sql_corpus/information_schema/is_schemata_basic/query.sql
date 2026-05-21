SELECT schema_name, location
FROM `region-us.INFORMATION_SCHEMA.SCHEMATA`
WHERE schema_name = '${DATASET_ID}'
ORDER BY schema_name
