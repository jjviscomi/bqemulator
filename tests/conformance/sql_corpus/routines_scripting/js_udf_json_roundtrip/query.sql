CREATE TEMP FUNCTION js_repack(s STRING) RETURNS STRING LANGUAGE js AS "var obj = JSON.parse(s); obj.added = true; return JSON.stringify(obj);";
SELECT js_repack('{"k":1}') AS payload
