WITH a AS (
  SELECT 1 AS k, 'left' AS lbl
  UNION ALL SELECT 2, 'middle'
  UNION ALL SELECT 3, 'right'
),
b AS (
  SELECT 1 AS k, 'p' AS x
  UNION ALL SELECT 2, 'q'
  UNION ALL SELECT 3, 'r'
)
SELECT a.lbl, b.x
FROM a
JOIN b ON a.k = b.k AND a.k = ?
ORDER BY a.k
