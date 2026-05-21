CREATE MATERIALIZED VIEW `${DATASET}.sales_by_country`
AS SELECT country, SUM(amount) AS total, COUNT(*) AS n
FROM `${DATASET}.sales` GROUP BY country;

SELECT country, total, n FROM `${DATASET}.sales_by_country` ORDER BY country
