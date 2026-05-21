SELECT
  ps.ps_partkey,
  ROUND(SUM(ps.ps_supplycost * ps.ps_availqty), 4) AS value
FROM `${DATASET}.partsupp` AS ps
JOIN `${DATASET}.supplier` AS s ON ps.ps_suppkey = s.s_suppkey
JOIN `${DATASET}.nation` AS n   ON s.s_nationkey = n.n_nationkey
WHERE n.n_name = 'GERMANY'
GROUP BY ps.ps_partkey
HAVING SUM(ps.ps_supplycost * ps.ps_availqty) > (
    SELECT SUM(ps2.ps_supplycost * ps2.ps_availqty) * 0.0001
    FROM `${DATASET}.partsupp` AS ps2
    JOIN `${DATASET}.supplier` AS s2 ON ps2.ps_suppkey = s2.s_suppkey
    JOIN `${DATASET}.nation` AS n2   ON s2.s_nationkey = n2.n_nationkey
    WHERE n2.n_name = 'GERMANY'
)
ORDER BY value DESC
