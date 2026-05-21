CREATE OR REPLACE TABLE `${DATASET}.docs` AS
SELECT 1 AS id, 'alice@example.com' AS owner_email, 'Q1 plan' AS title UNION ALL
SELECT 2, 'alice@example.com', 'Q2 plan' UNION ALL
SELECT 3, 'bob@example.com', 'Roadmap' UNION ALL
SELECT 4, 'carol@example.com', 'Notes';
