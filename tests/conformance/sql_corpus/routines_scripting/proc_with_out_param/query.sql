DECLARE result INT64;
CREATE OR REPLACE PROCEDURE `${DATASET}`.p_out_double(IN x INT64, OUT y INT64)
BEGIN
  SET y = x * 2;
END;
CALL `${DATASET}`.p_out_double(21, result);
SELECT result AS doubled
