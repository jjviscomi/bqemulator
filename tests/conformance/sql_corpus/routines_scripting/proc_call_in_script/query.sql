DECLARE total INT64 DEFAULT 0;
DECLARE tmp INT64;
CREATE OR REPLACE PROCEDURE `${DATASET}`.p_add(IN a INT64, IN b INT64, OUT s INT64)
BEGIN
  SET s = a + b;
END;
CALL `${DATASET}`.p_add(10, 5, tmp);
SET total = total + tmp;
CALL `${DATASET}`.p_add(total, 3, tmp);
SET total = tmp;
SELECT total AS final_value
