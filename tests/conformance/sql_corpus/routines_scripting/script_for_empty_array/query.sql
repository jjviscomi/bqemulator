DECLARE iterations INT64 DEFAULT 0;
FOR row IN (SELECT v FROM UNNEST(ARRAY<INT64>[]) AS v) DO
  SET iterations = iterations + 1;
END FOR;
SELECT iterations AS n
