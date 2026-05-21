SELECT table_name, use_standard_sql
FROM `${DATASET}.INFORMATION_SCHEMA.VIEWS`
ORDER BY table_name
