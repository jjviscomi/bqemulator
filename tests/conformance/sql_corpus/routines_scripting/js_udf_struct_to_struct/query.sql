CREATE TEMP FUNCTION js_swap(p STRUCT<a INT64, b STRING>) RETURNS STRUCT<a INT64, b STRING> LANGUAGE js AS "return {a: p.a * 10, b: p.b + '!'};";
SELECT js_swap(STRUCT(7 AS a, 'hi' AS b)) AS swapped
