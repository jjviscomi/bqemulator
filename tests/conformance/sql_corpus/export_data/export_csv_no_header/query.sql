EXPORT DATA OPTIONS (
  uri = 'gs://${GCS_BUCKET}/export/csv_no_header/*.csv',
  format = 'CSV',
  header = false,
  overwrite = true
) AS
SELECT 1 AS id, 'alpha' AS name
UNION ALL
SELECT 2 AS id, 'beta' AS name
UNION ALL
SELECT 3 AS id, 'gamma' AS name
