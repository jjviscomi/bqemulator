SELECT
  s.s_name,
  s.s_address
FROM `${DATASET}.supplier` AS s
JOIN `${DATASET}.nation` AS n ON s.s_nationkey = n.n_nationkey
WHERE s.s_suppkey IN (
    SELECT ps.ps_suppkey
    FROM `${DATASET}.partsupp` AS ps
    WHERE ps.ps_partkey IN (
        SELECT p.p_partkey
        FROM `${DATASET}.part` AS p
        WHERE p.p_name LIKE 'forest%'
      )
      AND ps.ps_availqty > (
        SELECT 0.5 * SUM(l.l_quantity)
        FROM `${DATASET}.lineitem` AS l
        WHERE l.l_partkey = ps.ps_partkey
          AND l.l_suppkey = ps.ps_suppkey
          AND l.l_shipdate >= DATE '1994-01-01'
          AND l.l_shipdate <  DATE '1995-01-01'
      )
  )
  AND n.n_name = 'CANADA'
ORDER BY s.s_name
