MERGE `${DATASET}`.target T
USING (SELECT 10 AS id, 'inserted-ten' AS label UNION ALL SELECT 20, 'inserted-twenty') S
  ON T.id = S.id
WHEN NOT MATCHED THEN INSERT (id, label) VALUES (S.id, S.label)
