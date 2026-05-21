SELECT table_name, table_type
FROM `${DATASET}.INFORMATION_SCHEMA.TABLES`
WHERE table_name = 'events'
