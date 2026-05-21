SELECT
  s.s_name,
  COUNT(*) AS numwait
FROM `${DATASET}.supplier` AS s
JOIN `${DATASET}.lineitem` AS l1 ON s.s_suppkey = l1.l_suppkey
JOIN `${DATASET}.orders` AS o    ON o.o_orderkey = l1.l_orderkey
JOIN `${DATASET}.nation` AS n    ON s.s_nationkey = n.n_nationkey
WHERE o.o_orderstatus = 'F'
  AND l1.l_receiptdate > l1.l_commitdate
  AND EXISTS (
    SELECT 1
    FROM `${DATASET}.lineitem` AS l2
    WHERE l2.l_orderkey = l1.l_orderkey
      AND l2.l_suppkey <> l1.l_suppkey
  )
  AND NOT EXISTS (
    SELECT 1
    FROM `${DATASET}.lineitem` AS l3
    WHERE l3.l_orderkey = l1.l_orderkey
      AND l3.l_suppkey <> l1.l_suppkey
      AND l3.l_receiptdate > l3.l_commitdate
  )
  AND n.n_name = 'SAUDI ARABIA'
GROUP BY s.s_name
ORDER BY numwait DESC, s.s_name
LIMIT 100
