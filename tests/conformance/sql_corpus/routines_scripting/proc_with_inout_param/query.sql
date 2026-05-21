DECLARE counter INT64 DEFAULT 10;
CREATE OR REPLACE PROCEDURE `${DATASET}`.p_inout_incr(INOUT n INT64)
BEGIN
  SET n = n + 5;
END;
CALL `${DATASET}`.p_inout_incr(counter);
SELECT counter AS after_call
