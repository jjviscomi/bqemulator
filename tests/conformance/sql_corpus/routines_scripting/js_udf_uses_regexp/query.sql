CREATE TEMP FUNCTION js_has_digits(s STRING) RETURNS BOOL LANGUAGE js AS "return /\\d+/.test(s);";
SELECT js_has_digits('abc123') AS has_digits, js_has_digits('plain text') AS no_digits
