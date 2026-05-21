DECLARE result STRING;
CREATE OR REPLACE PROCEDURE `${DATASET}`.greet(IN val STRING, OUT res STRING)
BEGIN
  SET res = CONCAT('hello:', IFNULL(val, '<null>'));
END;
CALL `${DATASET}`.greet(CAST(NULL AS STRING), result);
SELECT result AS s
