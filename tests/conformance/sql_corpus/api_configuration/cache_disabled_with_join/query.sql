SELECT l.n AS left_n, r.label AS right_label
FROM (SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3) AS l
JOIN (SELECT 1 AS n, 'one' AS label UNION ALL SELECT 2, 'two') AS r
  USING (n)
ORDER BY left_n
