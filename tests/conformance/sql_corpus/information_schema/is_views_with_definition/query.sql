SELECT table_name, use_standard_sql
FROM `${DATASET}.INFORMATION_SCHEMA.VIEWS`
WHERE table_name = 'v_def'
