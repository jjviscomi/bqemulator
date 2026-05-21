CREATE TABLE FUNCTION `${DATASET}`.orders_above(min_qty INT64)
  AS (SELECT id, qty FROM `${DATASET}`.orders WHERE qty >= min_qty);
SELECT t.id, t.qty, p.name
FROM `${DATASET}`.orders_above(20) AS t
JOIN `${DATASET}`.products AS p ON p.id = t.id
ORDER BY t.id
