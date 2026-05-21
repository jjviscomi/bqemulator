SELECT table_name, table_type, is_insertable_into, is_typed
FROM `${DATASET}.INFORMATION_SCHEMA.TABLES`
ORDER BY table_name
