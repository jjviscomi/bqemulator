FOR row IN (SELECT label, value FROM UNNEST([
  STRUCT('a' AS label, 1 AS value),
  STRUCT('b', 2),
  STRUCT('c', 3)
]) ORDER BY label) DO
  INSERT INTO `${DATASET}.for_accumulator` (label, doubled) VALUES (row.label, row.value * 2);
END FOR;
SELECT label, doubled FROM `${DATASET}.for_accumulator` ORDER BY label
