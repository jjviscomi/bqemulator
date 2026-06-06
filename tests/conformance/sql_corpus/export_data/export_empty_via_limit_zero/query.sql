EXPORT DATA OPTIONS (
  uri = 'gs://${GCS_BUCKET}/export/empty_via_limit_zero/*.csv',
  format = 'CSV',
  overwrite = true
) AS
SELECT 1 AS id, 'alpha' AS name
LIMIT 0
