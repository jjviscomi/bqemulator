CREATE TABLE FUNCTION `${DATASET}`.items_below(n INT64)
  AS (SELECT id, label FROM `${DATASET}`.items WHERE id < n);
SELECT id, label FROM `${DATASET}`.items_below(4) ORDER BY id
