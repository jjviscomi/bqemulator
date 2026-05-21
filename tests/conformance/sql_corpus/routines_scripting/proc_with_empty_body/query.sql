CREATE OR REPLACE PROCEDURE `${DATASET}`.do_nothing()
BEGIN
END;
CALL `${DATASET}`.do_nothing();
SELECT 1 AS sentinel
