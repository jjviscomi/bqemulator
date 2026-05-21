INSERT INTO `${DATASET}`.target (id, label)
SELECT id, label
FROM UNNEST([STRUCT(10 AS id, 'ten' AS label), STRUCT(20, 'twenty')])
