CREATE TEMP FUNCTION addone(x INT64) AS (x + 1);
SELECT addone(41) AS n
