SELECT option_name, option_type
FROM `${DATASET}.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE table_name = 't_opts_pf' AND option_name = 'require_partition_filter'
