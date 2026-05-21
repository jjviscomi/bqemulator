CREATE TEMP FUNCTION js_shout(s STRING) RETURNS STRING LANGUAGE js AS "return s.toUpperCase() + '!';";
SELECT js_shout('hello') AS msg
