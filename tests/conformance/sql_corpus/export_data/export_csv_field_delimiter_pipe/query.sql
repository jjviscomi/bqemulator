EXPORT DATA OPTIONS (
  uri = 'gs://${GCS_BUCKET}/export/csv_field_delimiter_pipe/*.csv',
  format = 'CSV',
  field_delimiter = '|',
  overwrite = true
) AS
SELECT 1 AS id, 'alpha' AS name
UNION ALL
SELECT 2 AS id, 'beta' AS name
UNION ALL
SELECT 3 AS id, 'gamma' AS name
