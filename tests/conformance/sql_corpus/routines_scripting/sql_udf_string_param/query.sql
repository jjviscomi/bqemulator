CREATE TEMP FUNCTION shout(s STRING) AS (CONCAT(UPPER(s), '!'));
SELECT shout('hello') AS msg
