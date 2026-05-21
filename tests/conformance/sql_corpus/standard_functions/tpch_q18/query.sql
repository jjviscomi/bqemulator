SELECT
  c.c_name,
  c.c_custkey,
  o.o_orderkey,
  o.o_orderdate,
  o.o_totalprice,
  SUM(l.l_quantity) AS sum_qty
FROM `${DATASET}.customer` AS c
JOIN `${DATASET}.orders` AS o   ON c.c_custkey = o.o_custkey
JOIN `${DATASET}.lineitem` AS l ON o.o_orderkey = l.l_orderkey
WHERE o.o_orderkey IN (
    SELECT l2.l_orderkey
    FROM `${DATASET}.lineitem` AS l2
    GROUP BY l2.l_orderkey
    HAVING SUM(l2.l_quantity) > 300
)
GROUP BY c.c_name, c.c_custkey, o.o_orderkey, o.o_orderdate, o.o_totalprice
ORDER BY o.o_totalprice DESC, o.o_orderdate
LIMIT 100
