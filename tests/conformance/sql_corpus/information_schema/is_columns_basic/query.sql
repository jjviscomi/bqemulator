SELECT column_name, ordinal_position, is_nullable, data_type
FROM `${DATASET}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'users'
ORDER BY ordinal_position
