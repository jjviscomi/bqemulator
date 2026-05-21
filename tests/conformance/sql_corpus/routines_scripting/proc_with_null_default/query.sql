DECLARE result STRING;
CREATE OR REPLACE PROCEDURE `${DATASET}`.with_default(IN val STRING, OUT res STRING)
BEGIN
  SET res = IFNULL(val, 'default-branch');
END;
CALL `${DATASET}`.with_default(CAST(NULL AS STRING), result);
SELECT result AS s
