CREATE TEMP FUNCTION js_maybe(x INT64) RETURNS INT64 LANGUAGE js AS "return x > 0 ? x : null;";
SELECT js_maybe(0) AS zero_case, js_maybe(5) AS pos_case
