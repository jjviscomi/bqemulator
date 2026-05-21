SELECT option_name, option_value
FROM `${DATASET}.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE table_name = 't_opts_desc' AND option_name = 'description'
