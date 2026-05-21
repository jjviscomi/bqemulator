CREATE TEMP FUNCTION js_bucket(x INT64) RETURNS STRING LANGUAGE js AS "return x % 2 === 0 ? 'even' : 'odd';";
SELECT js_bucket(n) AS bucket, COUNT(*) AS cnt
FROM UNNEST([1, 2, 3, 4, 5, 6]) AS n
GROUP BY js_bucket(n)
ORDER BY bucket
