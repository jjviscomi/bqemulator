DECLARE total INT64 DEFAULT 0;
DECLARE null_count INT64 DEFAULT 0;
FOR row IN (SELECT v FROM UNNEST([1, CAST(NULL AS INT64), 2, CAST(NULL AS INT64), 3]) AS v) DO
  IF row.v IS NULL THEN
    SET null_count = null_count + 1;
  ELSE
    SET total = total + row.v;
  END IF;
END FOR;
SELECT total AS sum_non_null, null_count AS null_count
