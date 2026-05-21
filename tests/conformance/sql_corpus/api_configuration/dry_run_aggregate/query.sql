SELECT label, SUM(n) AS total
FROM (SELECT 'a' AS label, 1 AS n UNION ALL SELECT 'a', 2 UNION ALL SELECT 'b', 3) t
GROUP BY label
ORDER BY label
