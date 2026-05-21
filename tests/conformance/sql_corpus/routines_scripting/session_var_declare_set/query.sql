DECLARE x INT64 DEFAULT 0;
SET x = x + 1;
SET x = x + 1;
SET x = x * 10;
SELECT x AS final_value
