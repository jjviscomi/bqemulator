CREATE TEMP FUNCTION js_boom(x INT64) RETURNS INT64 LANGUAGE js AS "throw new Error('boom from js udf');";
SELECT js_boom(1) AS r
