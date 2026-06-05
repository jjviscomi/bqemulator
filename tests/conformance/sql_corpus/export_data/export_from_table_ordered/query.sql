EXPORT DATA OPTIONS (
  uri = 'gs://${GCS_BUCKET}/export/from_table_ordered/*.csv',
  format = 'CSV',
  overwrite = true
) AS
SELECT id, val FROM `${DATASET}.export_src` ORDER BY id
