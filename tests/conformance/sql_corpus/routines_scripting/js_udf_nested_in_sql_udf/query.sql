CREATE TEMP FUNCTION js_triple(x INT64) RETURNS INT64 LANGUAGE js AS "return x * 3;";
CREATE TEMP FUNCTION sql_triple_plus_one(x INT64) AS (js_triple(x) + 1);
SELECT sql_triple_plus_one(4) AS r
