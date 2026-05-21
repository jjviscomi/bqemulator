CREATE MATERIALIZED VIEW `${DATASET}.user_totals`
AS SELECT u.name AS name, SUM(o.amount) AS total, COUNT(*) AS n
FROM `${DATASET}.users` u JOIN `${DATASET}.orders` o USING (user_id)
GROUP BY u.name;

SELECT name, total, n FROM `${DATASET}.user_totals` ORDER BY name
