SELECT
  o.o_orderpriority,
  COUNT(*) AS order_count
FROM `${DATASET}.orders` AS o
WHERE o.o_orderdate >= DATE '1993-07-01'
  AND o.o_orderdate < DATE '1993-10-01'
  AND EXISTS (
    SELECT 1
    FROM `${DATASET}.lineitem` AS l
    WHERE l.l_orderkey = o.o_orderkey
      AND l.l_commitdate < l.l_receiptdate
  )
GROUP BY o.o_orderpriority
ORDER BY o.o_orderpriority
