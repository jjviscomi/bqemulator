DECLARE outcome STRING DEFAULT 'ok';
BEGIN
  BEGIN TRANSACTION;
  INSERT INTO `${DATASET}`.ledger (id, amount) VALUES (1, 100);
  -- Force an error inside the transaction; the exception handler runs
  -- and BigQuery rolls back the implicit-open transaction.
  EXECUTE IMMEDIATE 'SELECT 1 / 0';
  COMMIT TRANSACTION;
EXCEPTION WHEN ERROR THEN
  SET outcome = 'caught';
END;
SELECT outcome AS outcome, (SELECT COUNT(*) FROM `${DATASET}`.ledger) AS row_count
