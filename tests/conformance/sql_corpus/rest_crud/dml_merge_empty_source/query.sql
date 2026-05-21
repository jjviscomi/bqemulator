MERGE `${DATASET}.target` t
USING `${DATASET}.source` s
ON t.id = s.id
WHEN MATCHED THEN UPDATE SET v = s.v
WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);
SELECT id, v FROM `${DATASET}.target` ORDER BY id
