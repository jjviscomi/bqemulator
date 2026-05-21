SELECT
  l.l_orderkey,
  ROUND(SUM(l.l_extendedprice * (1 - l.l_discount)), 4) AS revenue,
  o.o_orderdate,
  o.o_orderpriority
FROM `${DATASET}.customer` AS c
JOIN `${DATASET}.orders` AS o ON c.c_custkey = o.o_custkey
JOIN `${DATASET}.lineitem` AS l ON l.l_orderkey = o.o_orderkey
WHERE c.c_mktsegment = 'BUILDING'
  AND o.o_orderdate < DATE '1995-12-31'
  AND l.l_shipdate > DATE '1995-01-01'
GROUP BY l.l_orderkey, o.o_orderdate, o.o_orderpriority
ORDER BY revenue DESC, o.o_orderdate
LIMIT 10
