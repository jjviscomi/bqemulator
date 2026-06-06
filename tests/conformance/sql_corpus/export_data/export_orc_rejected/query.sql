EXPORT DATA OPTIONS (
  uri = 'gs://${GCS_BUCKET}/export/orc_rejected/*.orc',
  format = 'ORC',
  overwrite = true
) AS
SELECT 1 AS id, 'alpha' AS name
