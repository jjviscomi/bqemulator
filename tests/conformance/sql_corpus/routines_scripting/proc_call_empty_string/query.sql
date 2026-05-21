DECLARE result STRING;
CREATE OR REPLACE PROCEDURE `${DATASET}`.echo_str(IN val STRING, OUT res STRING)
BEGIN
  SET res = CONCAT('[', val, ']');
END;
CALL `${DATASET}`.echo_str('', result);
SELECT result AS s
