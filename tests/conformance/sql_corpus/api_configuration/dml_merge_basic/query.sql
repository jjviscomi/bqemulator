MERGE `${DATASET}`.target T
USING (SELECT 1 AS id, 'new' AS label UNION ALL SELECT 2, 'inserted') S
  ON T.id = S.id
WHEN MATCHED THEN UPDATE SET label = S.label
WHEN NOT MATCHED THEN INSERT (id, label) VALUES (S.id, S.label)
