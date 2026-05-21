CREATE TABLE FUNCTION `${DATASET}`.shift_by(offset_val INT64)
  AS (SELECT v, v + IFNULL(offset_val, 0) AS shifted FROM UNNEST([1, 2, 3]) AS v);
SELECT v, shifted FROM `${DATASET}`.shift_by(CAST(NULL AS INT64)) ORDER BY v
