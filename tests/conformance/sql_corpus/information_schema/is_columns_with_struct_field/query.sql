SELECT column_name, data_type
FROM `${DATASET}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'profiles'
ORDER BY ordinal_position
