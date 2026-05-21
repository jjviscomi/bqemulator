CREATE TEMP FUNCTION js_addone(x INT64) RETURNS INT64 LANGUAGE js AS "return x + 1;";
SELECT js_addone(41) AS n
