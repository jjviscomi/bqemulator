SELECT option_name, option_type
FROM `${DATASET}.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE table_name = 't_opts_basic'
ORDER BY option_name
