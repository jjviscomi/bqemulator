-- The runner provisions the dataset; this no-op query forces
-- setup.sql to be non-empty (a comment-only file is rejected by
-- BigQuery's parser). The query.sql below references a table that
-- does not exist.
SELECT 1 AS placeholder;
